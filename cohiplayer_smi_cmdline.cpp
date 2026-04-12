#include <iostream>
#include <fstream>
#include <cstdint>
#include <string>
#include <vector>
#include <cmath>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <liquid/liquid.h>

#pragma pack(push, 1)
struct ChunkHeader { char id[4]; uint32_t size; };
struct RiffHeader { char chunkId[4]; uint32_t chunkSize; char format[4]; };
struct FmtStruct { uint16_t audioFormat; uint16_t numChannels; uint32_t sampleRate; uint32_t byteRate; uint16_t blockAlign; uint16_t bitsPerSample; };
struct AuxiContent { uint8_t padding[68]; char filename[96]; };
#pragma pack(pop)

int main(int argc, char* argv[]) {
    // Parameter: <datei> <ip> <bitbreite (8,10,12,14,16)>
    if (argc < 3) {
        std::cerr << "Benutzung: " << argv[0] << " <datei.wav> <Ziel-IP> [Bitbreite (8,10,12,14,16 - Standard 8)]" << std::endl;
        return 1;
    }

    int targetBits = (argc > 3) ? std::stoi(argv[3]) : 8;
    float bitScale = powf(2.0f, targetBits - 1) - 1.0f; // z.B. 127 für 8-bit, 2047 für 12-bit

    std::ifstream file(argv[1], std::ios::binary);
    if (!file) { std::cerr << "Fehler: Datei nicht gefunden!" << std::endl; return 1; }

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in serv_addr = { .sin_family = AF_INET, .sin_port = htons(1234) };
    inet_pton(AF_INET, argv[2], &serv_addr.sin_addr);

    std::cout << "Verbinde zu " << argv[2] << "..." << std::endl;
    if (connect(sock, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0) {
        std::cerr << "TCP Verbindung fehlgeschlagen!" << std::endl;
        return 1;
    }

    RiffHeader riff;
    file.read(reinterpret_cast<char*>(&riff), sizeof(RiffHeader));

    uint32_t sampleRate = 0;
    float centerFreq = 0.0f;
    ChunkHeader chunk;

    // AGC Setup
    float current_gain = bitScale * 0.9f;
    float peak_hold = 0.1f;

    while (file.read(reinterpret_cast<char*>(&chunk), sizeof(ChunkHeader))) {
        std::string tag(chunk.id, 4);
        if (tag == "fmt ") {
            FmtStruct fmt;
            file.read(reinterpret_cast<char*>(&fmt), sizeof(FmtStruct));
            sampleRate = fmt.sampleRate;
            file.seekg(chunk.size - sizeof(FmtStruct), std::ios::cur);
        }
        else if (tag == "auxi") {
            AuxiContent aux;
            file.read(reinterpret_cast<char*>(&aux), sizeof(AuxiContent));
            std::string name(aux.filename);
            size_t khzPos = name.find("kHz");
            if (khzPos != std::string::npos) {
                size_t start = name.find_last_of("_ ", khzPos);
                if (start == std::string::npos) start = 0; else start++;
                try { centerFreq = std::stof(name.substr(start, khzPos - start)) * 1000.0f; }
                catch (...) { centerFreq = 0.0f; }
            }
        }
        else if (tag == "data") {
            const float targetRate = 5000000.0f;
            float upRate = targetRate / (float)sampleRate;

            // --- DEIN INFORMATIVES INTERFACE ---
            std::cout << "--- DSP Status ---" << std::endl;
            std::cout << "Eingangsrate: " << sampleRate << " Hz" << std::endl;
            std::cout << "Zielrate:     " << targetRate << " Hz" << std::endl;
            std::cout << "Trägershift:  " << centerFreq << " Hz" << std::endl;
            std::cout << "F-Norm (NCO): " << (centerFreq / targetRate) << std::endl;
            std::cout << "Ausgabe:      " << targetBits << " Bit (Skalierung: +/-" << bitScale << ")" << std::endl;

            msresamp_crcf resamp = msresamp_crcf_create(upRate, 60.0f);
            nco_crcf vco = nco_crcf_create(LIQUID_VCO);
            nco_crcf_set_frequency(vco, 2.0f * M_PI * (centerFreq / targetRate));

            const size_t blockSize = 1024;
            std::vector<int16_t> readBuf(blockSize * 2);
            liquid_float_complex x[blockSize];

            size_t outSize = (size_t)(blockSize * upRate) + 512;
            std::vector<liquid_float_complex> y(outSize);

            // Wir nutzen int16_t für das Netzwerk, um Bitbreiten > 8 zu unterstützen
            std::vector<int16_t> netBuf(outSize);
            unsigned int nw;

            while (file.read(reinterpret_cast<char*>(readBuf.data()), blockSize * 4)) {
                float block_peak = 0.0001f;

                for (int i = 0; i < blockSize; i++) {
                    x[i] = { (float)readBuf[2*i] / 32768.0f, (float)readBuf[2*i+1] / 32768.0f };
                    float mag = sqrtf(x[i].real*x[i].real + x[i].imag*x[i].imag);
                    if (mag > block_peak) block_peak = mag;
                }

                // AGC auf Ziel-Bitbreite bezogen
                peak_hold = 0.95f * peak_hold + 0.05f * block_peak;
                float target_gain = (bitScale * 0.85f) / (peak_hold + 0.0001f);
                current_gain = 0.98f * current_gain + 0.02f * target_gain;

                msresamp_crcf_execute(resamp, x, blockSize, y.data(), &nw);

                for (unsigned int j = 0; j < nw; j++) {
                    float c = nco_crcf_cos(vco);
                    float s = nco_crcf_sin(vco);
                    nco_crcf_step(vco);

                    float hf_val = (y[j].real * c - y[j].imag * s) * current_gain;

                    // Dynamisches Clipping je nach Bitbreite
                    if (hf_val > bitScale) hf_val = bitScale;
                    else if (hf_val < -bitScale) hf_val = -bitScale;

                    netBuf[j] = (int16_t)hf_val;
                }

                // Wenn 8 Bit gewählt, senden wir nur Bytes, sonst Shorts (2 Bytes pro Sample)
                if (targetBits == 8) {
                    // Konvertierung zurück auf 8-bit für Kompatibilität mit fl2k_tcp 8-bit
                    std::vector<int8_t> netBuf8(nw);
                    for(unsigned int k=0; k<nw; k++) netBuf8[k] = (int8_t)netBuf[k];
                    send(sock, netBuf8.data(), nw, 0);
                } else {
                    // Sende 16-bit Puffer (2 * nw Bytes)
                    send(sock, netBuf.data(), nw * 2, 0);
                }
            }
            std::cout << "Streaming beendet." << std::endl;
            msresamp_crcf_destroy(resamp);
            nco_crcf_destroy(vco);
            break;
        }
        else { file.seekg(chunk.size, std::ios::cur); }
        if (chunk.size % 2 != 0) file.seekg(1, std::ios::cur);
    }
    close(sock);
    return 0;
}
