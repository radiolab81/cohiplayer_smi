import tkinter as tk
from tkinter import ttk
import socket
import threading
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from collections import deque
import time

# --- KONFIGURATION ---
TCP_IP = "0.0.0.0"
TCP_PORT = 1234
SAMPLE_RATE = 5e6
FFT_SIZE = 2048     
WATERFALL_HISTORY = 100

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("SDR FFT Monitor (0-2.5 MHz)")
        
        self.data_queue = deque(maxlen=15)
        self.freqs_khz = np.fft.rfftfreq(FFT_SIZE, 1/SAMPLE_RATE) / 1e3
        self.num_bins = len(self.freqs_khz)
        
        # Daten-Speicher
        self.waterfall_data = np.full((WATERFALL_HISTORY, self.num_bins), -100.0)
        self.peak_hold_data = np.full(self.num_bins, -100.0)
        self.show_peak_hold = tk.BooleanVar(value=True)
        
        self.setup_ui()
        self.running = True
        
        self.thread = threading.Thread(target=self.tcp_reader, daemon=True)
        self.thread.start()
        self.update_plot()

    def setup_ui(self):
        # Main Layout: Links Plot, Rechts Controls
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=1)
        
        # Linker Bereich für Plot + Toolbar
        self.plot_frame = tk.Frame(self.main_frame)
        self.plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
        
        # Rechter Bereich für Regler
        self.ctrl_frame = tk.Frame(self.main_frame, width=220, padx=10, pady=10, relief=tk.RIDGE, borderwidth=1)
        self.ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.ctrl_frame.pack_propagate(False)

        # --- MATPLOTLIB SETUP ---
        self.fig = Figure(figsize=(8, 8), dpi=100)
        self.fig.subplots_adjust(hspace=0.3, bottom=0.1)
        
        self.ax_fft = self.fig.add_subplot(211)
        self.ax_fft.set_ylabel("dB")
        self.ax_fft.grid(True, alpha=0.3)
        self.ax_fft.set_ylim(-10, 90)
        self.ax_fft.set_facecolor('black')
        
        self.line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='cyan')
        self.peak_line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='red', alpha=0.5)
        
        self.ax_wf = self.fig.add_subplot(212, sharex=self.ax_fft)
        self.ax_wf.set_xlabel("Frequenz (kHz)")
        self.im = self.ax_wf.imshow(self.waterfall_data, aspect='auto', 
                                    extent=[self.freqs_khz[0], self.freqs_khz[-1], WATERFALL_HISTORY, 0], 
                                    cmap='magma', vmin=0, vmax=70)
        
        # Canvas anzeigen
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        # Toolbar explizit einfügen
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.plot_frame)
        self.toolbar.update()

        # --- CONTROLS (RECHTS) ---
        tk.Label(self.ctrl_frame, text="SPEKTRUM SETUP", font=('Arial', 10, 'bold')).pack(pady=10)
        
        self.cursor_label = tk.Label(self.ctrl_frame, text="Frequenz: --- kHz\nPegel: --- dB", 
                                     justify=tk.LEFT, fg="darkblue", font=('Courier', 10), bg="#eee", padx=5, pady=5)
        self.cursor_label.pack(fill=tk.X, pady=10)

        tk.Checkbutton(self.ctrl_frame, text="Peak Hold", variable=self.show_peak_hold).pack(anchor=tk.W)
        tk.Button(self.ctrl_frame, text="Reset Peak", command=self.reset_peak).pack(fill=tk.X, pady=5)

        tk.Label(self.ctrl_frame, text="\nWf Helligkeit (Max):").pack(anchor=tk.W)
        self.vmax_scale = tk.Scale(self.ctrl_frame, from_=0, to=120, orient=tk.HORIZONTAL)
        self.vmax_scale.set(80)
        self.vmax_scale.pack(fill=tk.X)

        tk.Label(self.ctrl_frame, text="Wf Boden (Min):").pack(anchor=tk.W)
        self.vmin_scale = tk.Scale(self.ctrl_frame, from_=-50, to=50, orient=tk.HORIZONTAL)
        self.vmin_scale.set(30)
        self.vmin_scale.pack(fill=tk.X)

        self.canvas.mpl_connect('button_press_event', self.on_click)

    def on_click(self, event):
        if event.inaxes:
            self.cursor_label.config(text=f"Frequenz: {event.xdata:.1f} kHz\nPegel: {event.ydata:.1f} dB")

    def reset_peak(self):
        self.peak_hold_data = np.full(self.num_bins, -100.0)

    def tcp_reader(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", TCP_PORT)) # Lausche auf allen Schnittstellen
        server_sock.listen(1)
        print(f"Debugger wartet auf Daten an Port {TCP_PORT}...")

        while self.running:
            try:
                conn, addr = server_sock.accept() # Hier wartet er auf Cohi
                print(f"Verbindung von {addr} akzeptiert!")
                raw_buffer = bytearray()
                while self.running:
                    chunk = conn.recv(65536) # Größere Chunks für 5 MSPS
                    if not chunk: break
                    raw_buffer.extend(chunk)
                    while len(raw_buffer) >= FFT_SIZE:
                        data_block = raw_buffer[:FFT_SIZE]
                        del raw_buffer[:FFT_SIZE]
                        self.data_queue.append(np.frombuffer(data_block, dtype=np.int8).astype(np.float32))
                conn.close()
            except Exception as e:
                print(f"Fehler: {e}")
                time.sleep(1)



    def update_plot(self):
        if self.data_queue:
            samples = self.data_queue.popleft()
            fft_res = np.fft.rfft(samples * np.hanning(FFT_SIZE))
            fft_db = 20 * np.log10(np.abs(fft_res) + 1e-6)
            fft_db = fft_db[:self.num_bins]

            self.peak_hold_data = np.maximum(self.peak_hold_data, fft_db)
            self.line.set_ydata(fft_db)
            
            if self.show_peak_hold.get():
                self.peak_line.set_ydata(self.peak_hold_data)
                self.peak_line.set_visible(True)
            else:
                self.peak_line.set_visible(False)
            
            self.waterfall_data = np.roll(self.waterfall_data, 1, axis=0)
            self.waterfall_data[0, :] = fft_db
            self.im.set_array(self.waterfall_data)
            self.im.set_clim(vmin=self.vmin_scale.get(), vmax=self.vmax_scale.get())
            self.canvas.draw_idle()
            
        self.root.after(30, self.update_plot)

if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("1200x800")
    app = App(root)
    root.mainloop()
