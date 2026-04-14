#include <iostream>
#include <fstream>
#include <vector>
#include <string>
#include <filesystem>
#include <algorithm>
#include <ncurses.h>
#include <cmath>
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#include <liquid/liquid.h>
#include <thread>
#include <mutex>
#include <atomic>
#include <condition_variable>
#include <chrono>

namespace fs = std::filesystem;

// --- STRUKTUREN ---
#pragma pack(push, 1)
struct ChunkHeader { char id[4]; uint32_t size; };
struct RiffHeader { char chunkId[4]; uint32_t chunkSize; char format[4]; };
struct FmtStruct { uint16_t audioFormat; uint16_t numChannels; uint32_t sampleRate; uint32_t byteRate; uint16_t blockAlign; uint16_t bitsPerSample; };
struct AuxiContent { uint8_t padding[68]; char filename[96]; };
#pragma pack(pop)

struct FileEntry {
    std::string name;
    bool is_dir;
};

// --- SHARED STATE FÜR MULTITHREADING ---
struct SharedState {
    std::atomic<bool> app_running{true};
    std::atomic<bool> dsp_running{false};
    std::atomic<bool> stop_dsp{false};

    std::mutex mtx;
    std::string targetIP = "127.0.0.1";
    int targetBits = 10;
    fs::path currentPath;

    // DSP Stats für das UI
    std::string dsp_filename;
    std::string dsp_next_filename;
    std::atomic<float> sampleRate{0};
    std::atomic<float> centerFreq{0};
    std::atomic<float> peak_hold{0};
    std::atomic<float> progress{0};

    // Command/Trigger
    std::string file_to_play;
    std::string conn_error_msg;
    std::condition_variable cv_dsp;
};

SharedState state;


// --- DAC STEUERUNG (Port 5000) ---
void set_dac_width(std::string ip, int bits) {
    int ctrl_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (ctrl_sock < 0) return;

    struct sockaddr_in serv_addr = { .sin_family = AF_INET, .sin_port = htons(5000) };
    inet_pton(AF_INET, ip.c_str(), &serv_addr.sin_addr);

    struct timeval tv = { .tv_sec = 0, .tv_usec = 200000 };
    setsockopt(ctrl_sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    if (connect(ctrl_sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) >= 0) {
        std::string cmd = "width " + std::to_string(bits);
        send(ctrl_sock, cmd.c_str(), cmd.length(), 0);
    }
    close(ctrl_sock);
}

// --- HILFSFUNKTIONEN ---
std::vector<FileEntry> get_directory_content(fs::path p) {
    std::vector<FileEntry> current_files;
    current_files.push_back({"..", true});
    try {
        for (const auto & entry : fs::directory_iterator(p)) {
            if (entry.is_directory()) current_files.push_back({entry.path().filename().string(), true});
            else if (entry.path().extension() == ".wav") current_files.push_back({entry.path().filename().string(), false});
        }
    } catch (...) {}
    std::sort(current_files.begin() + 1, current_files.end(), [](const FileEntry& a, const FileEntry& b) {
        if (a.is_dir != b.is_dir) return a.is_dir > b.is_dir;
        return a.name < b.name;
    });
    return current_files;
}

std::string get_string_input(int y, int x, const std::string& prompt, const std::string& current) {
    echo(); curs_set(1);
    char input[64];
    mvprintw(y, x, "%s [%s]: ", prompt.c_str(), current.c_str());
    getstr(input);
    noecho(); curs_set(0);
    std::string res(input);
    return res.empty() ? current : res;
}

// --- DSP ENGINE ---
std::string run_dsp_engine(fs::path currentPath, std::string filename, int sock, int targetBits) {
    std::string nextFileFound = "";
    fs::path fullPath = currentPath / filename;
    std::ifstream file(fullPath, std::ios::binary);
    if (!file) return "";

    RiffHeader riff;
    if (!file.read(reinterpret_cast<char*>(&riff), sizeof(RiffHeader))) return "";

    uint32_t sampleRate = 0;
    float centerFreq = 0.0f;
    ChunkHeader chunk;
    float bitScale = powf(2.0f, targetBits - 1) - 1.0f;
    float current_gain = bitScale * 0.9f;
    float peak_hold = 0.1f;

    // Initiale UI-Status-Übergabe
    {
        std::lock_guard<std::mutex> lock(state.mtx);
        state.dsp_filename = filename;
        state.dsp_next_filename = "";
        state.sampleRate = 0;
        state.centerFreq = 0;
        state.peak_hold = 0.1f;
        state.progress = 0.0f;
    }

    while (file.read(reinterpret_cast<char*>(&chunk), sizeof(ChunkHeader))) {
        if (state.stop_dsp) { nextFileFound = ""; break; }

        std::string tag(chunk.id, 4);
        if (tag == "fmt ") {
            FmtStruct fmt;
            file.read(reinterpret_cast<char*>(&fmt), sizeof(FmtStruct));
            sampleRate = fmt.sampleRate;
            state.sampleRate = sampleRate; // Update atomic state
            if (chunk.size > sizeof(FmtStruct)) file.seekg(chunk.size - sizeof(FmtStruct), std::ios::cur);
        }
        else if (tag == "auxi") {
            std::vector<char> buffer(chunk.size);
            file.read(buffer.data(), chunk.size);
            if (chunk.size >= sizeof(AuxiContent)) {
                AuxiContent* aux = reinterpret_cast<AuxiContent*>(buffer.data());
                std::string rawName(aux->filename, sizeof(aux->filename));
                size_t last = rawName.find_last_not_of(" \t\n\r\0\x01", std::string::npos, 6);
                if (last != std::string::npos) {
                    nextFileFound = rawName.substr(0, last + 1);
                } else {
                    nextFileFound = "";
                }
                std::lock_guard<std::mutex> lock(state.mtx);
                state.dsp_next_filename = nextFileFound;
            }
        }
        else if (tag == "data") {
            // Mittenfrequenz für NCO aus AKTUELLER Datei extrahieren
            size_t khzPos = filename.find("kHz");
            if (khzPos != std::string::npos) {
                size_t start = filename.find_last_of("_ ", khzPos);
                if (start == std::string::npos) start = 0; else start++;
                try { centerFreq = std::stof(filename.substr(start, khzPos - start)) * 1000.0f; } catch (...) {}
            }
            state.centerFreq = centerFreq;

            uint32_t dataSize = chunk.size;
            uint32_t bytesRead = 0;
            const float targetRate = 5000000.0f;
            float upRate = targetRate / (float)sampleRate;
            msresamp_crcf resamp = msresamp_crcf_create(upRate, 60.0f);
            nco_crcf vco = nco_crcf_create(LIQUID_VCO);
            nco_crcf_set_frequency(vco, 2.0f * M_PI * (centerFreq / targetRate));

            const size_t blockSize = 1024;
            std::vector<int16_t> readBuf(blockSize * 2);
            size_t outSize = (size_t)(blockSize * upRate) + 512;
            std::vector<int8_t> nb8(outSize);

            std::vector<liquid_float_complex> x(blockSize), y(outSize);
            std::vector<int16_t> netBuf(outSize);
            unsigned int nw;

            while (file.read(reinterpret_cast<char*>(readBuf.data()), blockSize * 4)) {
                // Input Prüfung in den Worker-Loop verlagert (via state.stop_dsp)
                if (state.stop_dsp) { nextFileFound = ""; break; }

                bytesRead += blockSize * 4;
                float block_peak = 0.0001f;
                for (int i = 0; i < blockSize; i++) {
                    x[i] = { (float)readBuf[2*i] / 32768.0f, (float)readBuf[2*i+1] / 32768.0f };
                    float mag = sqrtf(x[i].real*x[i].real + x[i].imag*x[i].imag);
                    if (mag > block_peak) block_peak = mag;
                }
                peak_hold = 0.95f * peak_hold + 0.05f * block_peak;
                current_gain = 0.98f * current_gain + 0.02f * ((bitScale * 0.65f) / (peak_hold + 0.0001f));

                // UI-Werte atomar aktualisieren
                state.peak_hold = peak_hold;
                state.progress = (float)bytesRead / (float)dataSize * 100.0f;

                msresamp_crcf_execute(resamp, x.data(), blockSize, y.data(), &nw);

                for (unsigned int j = 0; j < nw; j++) {
                    float c = nco_crcf_cos(vco), s = nco_crcf_sin(vco); nco_crcf_step(vco);
                    float hf = (y[j].real * c - y[j].imag * s) * (current_gain * 0.24f);
                    if (hf > bitScale) hf = bitScale; else if (hf < -bitScale) hf = -bitScale;
                    netBuf[j] = (int16_t)hf;
                }

                ssize_t snt;
                if (targetBits == 8) {
                    for(unsigned int k=0; k<nw; k++) nb8[k] = (int8_t)netBuf[k];
                    snt = send(sock, nb8.data(), nw, MSG_NOSIGNAL);
                } else {
                    snt = send(sock, netBuf.data(), nw * 2, MSG_NOSIGNAL);
                }
                if (snt < 0) { nextFileFound = ""; break; }
            }
            msresamp_crcf_destroy(resamp); nco_crcf_destroy(vco);
            break;
        } else { file.seekg(chunk.size, std::ios::cur); }
        if (chunk.size % 2 != 0) file.seekg(1, std::ios::cur);
    }
    file.close();
    return nextFileFound;
}

// --- DSP THREAD (Worker) ---
void dsp_thread_func() {
    while(state.app_running) {
        std::string nxt_file;
        std::string ip;
        int bits;
        fs::path path;

        {
            std::unique_lock<std::mutex> lock(state.mtx);
            state.cv_dsp.wait(lock, []{ return !state.file_to_play.empty() || !state.app_running; });
            if (!state.app_running) break;

            nxt_file = state.file_to_play;
            state.file_to_play = ""; // Trigger zurücksetzen
            ip = state.targetIP;
            bits = state.targetBits;
            path = state.currentPath;
        }

        state.dsp_running = true;
        state.stop_dsp = false;

        int sock = socket(AF_INET, SOCK_STREAM, 0);
        struct sockaddr_in srv = { .sin_family = AF_INET, .sin_port = htons(1234) };
        inet_pton(AF_INET, ip.c_str(), &srv.sin_addr);

        if (connect(sock, (struct sockaddr *)&srv, sizeof(srv)) >= 0) {
            int flag = 1;
            if (setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, (char *)&flag, sizeof(int)) < 0) {
                perror("TCP_NODELAY konnte nicht gesetzt werden");
            }

            std::string nxt = nxt_file;
            while (!nxt.empty() && state.app_running && !state.stop_dsp) {
                nxt = run_dsp_engine(path, nxt, sock, bits);
                if (!nxt.empty() && !fs::exists(path / nxt)) break;
            }
            close(sock);
        } else {
            {
                std::lock_guard<std::mutex> lock(state.mtx);
                state.conn_error_msg = "Verbindung zu " + ip + " gescheitert!";
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1500));
            {
                std::lock_guard<std::mutex> lock(state.mtx);
                state.conn_error_msg = "";
            }
        }

        state.dsp_running = false;
        state.stop_dsp = false;
    }
}

// --- UI THREAD ---
void ui_thread_func() {
    initscr(); start_color(); noecho(); curs_set(0); keypad(stdscr, TRUE);
    nodelay(stdscr, TRUE); // Essentiell für non-blocking Updates
    init_pair(1, COLOR_YELLOW, COLOR_BLACK);
    init_pair(2, COLOR_GREEN, COLOR_BLACK);
    init_pair(3, COLOR_CYAN, COLOR_BLACK);

    int highlight = 0, scrollOffset = 0;

    while(state.app_running) {
        if (state.dsp_running) {
            // --- DSP ANSICHT ---
            erase();
            std::string fname, nxt;
            {
                std::lock_guard<std::mutex> lock(state.mtx);
                fname = state.dsp_filename;
                nxt = state.dsp_next_filename;
            }

            attron(COLOR_PAIR(1) | A_BOLD);
            mvprintw(1, 2, ">>> SENDET (GAPLESS): %s", fname.c_str());
            attroff(COLOR_PAIR(1) | A_BOLD);
            mvprintw(3, 2, "Rate: %.2f MHz | Shift: %.1f kHz", (float)state.sampleRate/1e6, state.centerFreq/1e3);

            float ph = state.peak_hold;
            int barLen = (int)((ph / 1.0f) * 40.0f);
            if (barLen > 40) barLen = 40;
            mvprintw(5, 2, "Pegel: [");
            attron(COLOR_PAIR(2));
            for(int b=0; b<barLen; b++) addch('#');
            attroff(COLOR_PAIR(2));
            for(int b=barLen; b<40; b++) addch('-');
            printw("] %.1f%%", ph * 100.0f);

            float prog = state.progress;
            mvprintw(6, 2, "Forts: [");
            int progLen = (int)(prog / 100.0f * 40.0f);
            for(int b=0; b<progLen; b++) addch('=');
            for(int b=progLen; b<40; b++) addch(' ');
            printw("] %.1f%%", prog);

            if (!nxt.empty()) mvprintw(8, 2, "naechste Datei: %s", nxt.c_str());
            mvprintw(LINES-2, 2, "Taste 'q' stoppt die Aussendung.");
            refresh();

            int c = getch();
            if (c == 'q' || c == 'Q') {
                state.stop_dsp = true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(16));

        } else {
            // --- BROWSER ANSICHT ---
            std::string current_ip;
            int current_bits;
            fs::path curr_path;
            std::string err_msg;

            {
                std::lock_guard<std::mutex> lock(state.mtx);
                current_ip = state.targetIP;
                current_bits = state.targetBits;
                curr_path = state.currentPath;
                err_msg = state.conn_error_msg;
            }

            auto entries = get_directory_content(curr_path);
            if (highlight >= entries.size()) highlight = 0;
            int maxRows = LINES - 6;

            clear();
            attron(COLOR_PAIR(3) | A_REVERSE);
            mvprintw(0, 0, " IP: %s | BITS: %-2d | [i] IP | [b] Bits | [q] Exit ", current_ip.c_str(), current_bits);
            attroff(COLOR_PAIR(3) | A_REVERSE);
            mvprintw(1, 0, " Pfad: %s", curr_path.string().c_str());

            for(int i = 0; i < maxRows && (i + scrollOffset) < entries.size(); i++) {
                int idx = i + scrollOffset;
                if(idx == highlight) attron(A_REVERSE | A_BOLD);
                mvprintw(3 + i, 2, "%s %s", entries[idx].is_dir ? "[DIR]" : "     ", entries[idx].name.c_str());
                if(idx == highlight) attroff(A_REVERSE | A_BOLD);
            }

            if (!err_msg.empty()) {
                mvprintw(LINES-2, 2, "%s", err_msg.c_str());
            }

            refresh();

            int c = getch();
            if (c == ERR) {
                std::this_thread::sleep_for(std::chrono::milliseconds(16));
                continue;
            }

            if(c == KEY_UP) { if(highlight > 0) highlight--; if(highlight < scrollOffset) scrollOffset--; }
            else if(c == KEY_DOWN) { if(highlight < (int)entries.size() - 1) highlight++; if(highlight >= scrollOffset + maxRows) scrollOffset++; }
            else if(c == 'i') {
                nodelay(stdscr, FALSE);
                std::string newIP = get_string_input(LINES-2, 0, "Ziel-IP", current_ip);
                nodelay(stdscr, TRUE);
                std::lock_guard<std::mutex> lock(state.mtx);
                state.targetIP = newIP;
            }
            else if(c == 'b') {
                nodelay(stdscr, FALSE);
                std::string bStr = get_string_input(LINES-2, 0, "Bitbreite", std::to_string(current_bits));
                nodelay(stdscr, TRUE);
                try {
                     int tBits = std::stoi(bStr);
                     std::lock_guard<std::mutex> lock(state.mtx);
                     state.targetBits = tBits;
                     if (tBits == 8) set_dac_width(state.targetIP, 8);
                     else set_dac_width(state.targetIP, 16);
                } catch(...) {}
            }
            else if(c == 10) { // ENTER
                if(entries[highlight].name == "..") {
                    std::lock_guard<std::mutex> lock(state.mtx);
                    state.currentPath = state.currentPath.parent_path();
                    highlight = 0; scrollOffset = 0;
                }
                else if(entries[highlight].is_dir) {
                    std::lock_guard<std::mutex> lock(state.mtx);
                    state.currentPath /= entries[highlight].name;
                    highlight = 0; scrollOffset = 0;
                }
                else {
                    std::lock_guard<std::mutex> lock(state.mtx);
                    state.file_to_play = entries[highlight].name;
                    state.cv_dsp.notify_one(); // Worker wecken
                }
            }
            else if(c == 'q' || c == 'Q') {
                state.app_running = false;
            }
        }
    }
    endwin();
}

// --- MAIN ---
int main(int argc, char* argv[]) {
    // Initialen Pfad ermitteln
    state.currentPath = fs::current_path();

    // Threads starten
    std::thread dsp_t(dsp_thread_func);
    std::thread ui_t(ui_thread_func);

    // Auf Beendigung des UI-Threads warten (durch Taste 'q')
    ui_t.join();

    // Worker-Thread ordnungsgemäß herunterfahren
    state.app_running = false;
    state.cv_dsp.notify_all();
    dsp_t.join();

    return 0;
}