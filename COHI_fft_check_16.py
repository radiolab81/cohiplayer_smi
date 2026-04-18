import tkinter as tk
import socket
import threading
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from collections import deque
import time
import re

# --- INITIAL-KONFIGURATION ---
TCP_IP = "0.0.0.0"
DATA_PORT = 1234
CTRL_PORT = 5000

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("SDR Adaptive Monitor")
        
        # Dynamische Parameter (werden über Port 5000 gesteuert)
        self.sample_rate = 5e6
        self.bit_width = 8
        self.FFT_SIZE = 2048
        
        self.data_queue = deque(maxlen=15)
        self.running = True
        
        # Plot-Daten Speicher
        self.freqs_khz = np.fft.rfftfreq(self.FFT_SIZE, 1/self.sample_rate) / 1e3
        self.num_bins = len(self.freqs_khz)
        self.waterfall_history = 100
        self.waterfall_data = np.full((self.waterfall_history, self.num_bins), -100.0)
        self.peak_hold_data = np.full(self.num_bins, -100.0)
        self.show_peak_hold = tk.BooleanVar(value=True)

        self.setup_ui()
        
        # Thread 1: Lauscht auf Steuerbefehle (Port 5000)
        self.ctrl_thread = threading.Thread(target=self.control_server, daemon=True)
        self.ctrl_thread.start()

        # Thread 2: Lauscht auf Rohdaten (Port 1234)
        self.data_thread = threading.Thread(target=self.tcp_reader, daemon=True)
        self.data_thread.start()
        
        self.update_plot()

    def setup_ui(self):
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=1)
        
        self.plot_frame = tk.Frame(self.main_frame)
        self.plot_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
        
        self.ctrl_frame = tk.Frame(self.main_frame, width=220, padx=10, pady=10, relief=tk.RIDGE, borderwidth=1)
        self.ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.ctrl_frame.pack_propagate(False)

        # Plot Setup
        self.fig = Figure(figsize=(8, 8), dpi=100)
        self.ax_fft = self.fig.add_subplot(211)
        self.ax_fft.set_facecolor('black')
        self.ax_fft.set_ylim(-100, 20) # Angepasst für 16-Bit Scale
        
        self.line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='cyan')
        self.peak_line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='red', alpha=0.5)
        
        self.ax_wf = self.fig.add_subplot(212, sharex=self.ax_fft)
        self.im = self.ax_wf.imshow(self.waterfall_data, aspect='auto', cmap='magma', vmin=-80, vmax=0)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        # Info-Label für aktuelle Hardware-Settings
        self.status_label = tk.Label(self.ctrl_frame, text="Warte auf Quelle...", font=('Arial', 9, 'bold'), fg="blue")
        self.status_label.pack(pady=10)

        tk.Checkbutton(self.ctrl_frame, text="Peak Hold", variable=self.show_peak_hold).pack(anchor=tk.W)
        
        self.vmax_scale = tk.Scale(self.ctrl_frame, from_=-100, to=40, orient=tk.HORIZONTAL, label="Wf Max dB")
        self.vmax_scale.set(0)
        self.vmax_scale.pack(fill=tk.X)

        self.vmin_scale = tk.Scale(self.ctrl_frame, from_=-150, to=0, orient=tk.HORIZONTAL, label="Wf Min dB")
        self.vmin_scale.set(-80)
        self.vmin_scale.pack(fill=tk.X)

    def control_server(self):
        """Empfängt 'rate x.x' und 'width x' Befehle auf Port 5000"""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", CTRL_PORT))
        server_sock.listen(1)
        
        while self.running:
            conn, addr = server_sock.accept()
            try:
                data = conn.recv(1024).decode().strip()
                if not data: continue
                
                # Regex für "rate 12.5" oder "width 16"
                rate_match = re.search(r"rate\s+([\d\.]+)", data)
                width_match = re.search(r"width\s+(\d+)", data)
                
                if rate_match:
                    new_rate = float(rate_match.group(1)) * 1e6 # MHz to Hz
                    if new_rate != self.sample_rate:
                        self.sample_rate = new_rate
                        self.reinit_buffers()
                
                if width_match:
                    new_width = int(width_match.group(1))
                    if new_width != self.bit_width:
                        self.bit_width = new_width
                        print(f"Bitbreite gewechselt auf: {self.bit_width}")

                self.status_label.config(text=f"SR: {self.sample_rate/1e6:.1f} MHz\nBits: {self.bit_width}")
                
            except Exception as e:
                print(f"Control Error: {e}")
            finally:
                conn.close()

    def reinit_buffers(self):
        """Wird aufgerufen, wenn sich die Samplerate ändert"""
        self.freqs_khz = np.fft.rfftfreq(self.FFT_SIZE, 1/self.sample_rate) / 1e3
        self.num_bins = len(self.freqs_khz)
        self.waterfall_data = np.full((self.waterfall_history, self.num_bins), -100.0)
        self.peak_hold_data = np.full(self.num_bins, -100.0)
        
        # Plot-Elemente müssen im Main-Thread aktualisiert werden, 
        # das passiert automatisch beim nächsten update_plot Call durch die Zuweisung hier:
        self.line.set_xdata(self.freqs_khz)
        self.peak_line.set_xdata(self.freqs_khz)
        # Update extent des Wasserfalls
        self.im.set_extent([self.freqs_khz[0], self.freqs_khz[-1], self.waterfall_history, 0])

    def tcp_reader(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", DATA_PORT))
        server_sock.listen(1)
        
        while self.running:
            try:
                conn, addr = server_sock.accept()
                raw_buffer = bytearray()
                while self.running:
                    # Dynamische Blockgröße je nach Bitbreite
                    bytes_per_sample = 1 if self.bit_width == 8 else 2
                    block_size = self.FFT_SIZE * bytes_per_sample
                    
                    chunk = conn.recv(65536)
                    if not chunk: break
                    raw_buffer.extend(chunk)
                    
                    while len(raw_buffer) >= block_size:
                        data_block = raw_buffer[:block_size]
                        del raw_buffer[:block_size]
                        
                        dtype = np.int8 if self.bit_width == 8 else np.int16
                        # Skalierung auf -1.0 bis 1.0
                        scale = 128.0 if self.bit_width == 8 else 32768.0
                        
                        samples = np.frombuffer(data_block, dtype=dtype).astype(np.float32) / scale
                        self.data_queue.append(samples)
                conn.close()
            except Exception as e:
                time.sleep(1)

    def update_plot(self):
        if self.data_queue:
            # Wir nehmen das aktuellste Paket (wirf alte weg, falls zu langsam)
            while len(self.data_queue) > 1:
                self.data_queue.popleft()
                
            samples = self.data_queue.popleft()
            
            # FFT mit Hanning-Fenster
            fft_res = np.fft.rfft(samples * np.hanning(len(samples)))
            # dBFS Berechnung
            fft_db = 20 * np.log10(np.abs(fft_res) + 1e-9)
            
            # Falls sich num_bins durch reinit geändert hat, abschneiden/anpassen
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
