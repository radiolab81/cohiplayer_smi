import tkinter as tk
from tkinter import ttk
import socket
import threading
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from collections import deque
import time
import re

# --- KONFIGURATION ---
DATA_PORT = 1234
CTRL_PORT = 5000

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("SDR Adaptive Monitor")
        
        # Dynamische Parameter
        self.sample_rate = 5e6
        self.bit_width = 8
        self.FFT_SIZE = 2048
        
        self.data_queue = deque(maxlen=15)
        self.running = True
        
        # Frequenz-Achsen Setup
        self.freqs_khz = np.fft.rfftfreq(self.FFT_SIZE, 1/self.sample_rate) / 1e3
        self.num_bins = len(self.freqs_khz)
        self.waterfall_history = 100
        self.waterfall_data = np.full((self.waterfall_history, self.num_bins), -100.0)
        self.peak_hold_data = np.full(self.num_bins, -100.0)
        self.show_peak_hold = tk.BooleanVar(value=True)

        self.setup_ui()
        
        # Threads starten
        threading.Thread(target=self.control_server, daemon=True).start()
        threading.Thread(target=self.tcp_reader, daemon=True).start()
        
        self.update_plot()

    def setup_ui(self):
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=1)
        
        # Linker Bereich: Plot & Toolbar
        self.plot_container = tk.Frame(self.main_frame)
        self.plot_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=1)
        
        # Rechter Bereich: Controls
        self.ctrl_frame = tk.Frame(self.main_frame, width=220, padx=10, pady=10, relief=tk.RIDGE, borderwidth=1)
        self.ctrl_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.ctrl_frame.pack_propagate(False)

        # --- MATPLOTLIB SETUP ---
        self.fig = Figure(figsize=(8, 8), dpi=100)
        self.fig.subplots_adjust(hspace=0.3)
        
        # Spektrum Plot
        self.ax_fft = self.fig.add_subplot(211)
        self.ax_fft.set_facecolor('black')
        self.ax_fft.set_ylim(-110, 10)
        self.ax_fft.set_ylabel("dBFS")
        self.ax_fft.grid(True, color='gray', alpha=0.3)
        
        self.line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='cyan')
        self.peak_line, = self.ax_fft.plot(self.freqs_khz, np.zeros(self.num_bins), lw=1, color='red', alpha=0.5)
        
        # Wasserfall Plot
        self.ax_wf = self.fig.add_subplot(212, sharex=self.ax_fft)
        self.ax_wf.set_xlabel("Frequenz (kHz)")
        self.im = self.ax_wf.imshow(self.waterfall_data, aspect='auto', cmap='magma', 
                                    extent=[self.freqs_khz[0], self.freqs_khz[-1], self.waterfall_history, 0],
                                    vmin=-80, vmax=0)
        
        # Canvas & Mess-Toolbar
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_container)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        # DIE MESSWERKZEUGE (Toolbar)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self.plot_container)
        self.toolbar.update()
        
        # --- RECHTE SEITE CONTROLS ---
        tk.Label(self.ctrl_frame, text="STATUS / MESSWERTE", font=('Arial', 10, 'bold')).pack(pady=5)
        
        self.status_label = tk.Label(self.ctrl_frame, text="Warte auf Quelle...", fg="blue", justify=tk.LEFT)
        self.status_label.pack(fill=tk.X, pady=5)
        
        self.cursor_label = tk.Label(self.ctrl_frame, text="Cursor: ---", font=('Courier', 9), bg="black", fg="lime", padx=5)
        self.cursor_label.pack(fill=tk.X, pady=10)

        tk.Checkbutton(self.ctrl_frame, text="Peak Hold", variable=self.show_peak_hold).pack(anchor=tk.W)
        tk.Button(self.ctrl_frame, text="Reset Peak", command=self.reset_peak).pack(fill=tk.X, pady=5)

        self.vmax_scale = tk.Scale(self.ctrl_frame, from_=-100, to=40, orient=tk.HORIZONTAL, label="Wf Max dB")
        self.vmax_scale.set(0)
        self.vmax_scale.pack(fill=tk.X)

        self.vmin_scale = tk.Scale(self.ctrl_frame, from_=-150, to=0, orient=tk.HORIZONTAL, label="Wf Min dB")
        self.vmin_scale.set(-80)
        self.vmin_scale.pack(fill=tk.X)

        # Event-Binding für Messungen per Mausklick
        self.canvas.mpl_connect('button_press_event', self.on_click)

    def on_click(self, event):
        if event.inaxes:
            self.cursor_label.config(text=f"{event.xdata:.2f} kHz\n{event.ydata:.1f} dB")

    def reset_peak(self):
        self.peak_hold_data = np.full(self.num_bins, -100.0)

    def control_server(self):
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", CTRL_PORT))
        server_sock.listen(1)
        
        while self.running:
            try:
                conn, addr = server_sock.accept()
                data = conn.recv(1024).decode().strip()
                if not data: continue
                
                # Befehle parsen
                rate_match = re.search(r"rate\s+([\d\.]+)", data)
                width_match = re.search(r"width\s+(\d+)", data)
                
                if rate_match:
                    self.sample_rate = float(rate_match.group(1)) * 1e6
                if width_match:
                    self.bit_width = int(width_match.group(1))

                # GUI über Statusänderung informieren (Thread-safe)
                self.root.after(0, self.reinit_buffers)
                conn.close()
            except: pass

    def reinit_buffers(self):
        """Aktualisiert alle Achsen und Arrays bei Hardware-Wechsel"""
        self.freqs_khz = np.fft.rfftfreq(self.FFT_SIZE, 1/self.sample_rate) / 1e3
        self.num_bins = len(self.freqs_khz)
        self.waterfall_data = np.full((self.waterfall_history, self.num_bins), -100.0)
        self.peak_hold_data = np.full(self.num_bins, -100.0)
        
        self.line.set_xdata(self.freqs_khz)
        self.peak_line.set_xdata(self.freqs_khz)
        self.im.set_extent([self.freqs_khz[0], self.freqs_khz[-1], self.waterfall_history, 0])
        self.ax_fft.set_xlim(self.freqs_khz[0], self.freqs_khz[-1])
        
        self.status_label.config(text=f"Rate: {self.sample_rate/1e6} MHz\nBits: {self.bit_width}-Bit")
        self.canvas.draw_idle()

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
                    b_per_s = 1 if self.bit_width == 8 else 2
                    block_size = self.FFT_SIZE * b_per_s
                    
                    chunk = conn.recv(65536)
                    if not chunk: break
                    raw_buffer.extend(chunk)
                    
                    while len(raw_buffer) >= block_size:
                        data_block = raw_buffer[:block_size]
                        del raw_buffer[:block_size]
                        
                        dtype = np.int8 if self.bit_width == 8 else np.int16
                        scale = 128.0 if self.bit_width == 8 else 32768.0
                        
                        samples = np.frombuffer(data_block, dtype=dtype).astype(np.float32) / scale
                        self.data_queue.append(samples)
                conn.close()
            except: pass

    def update_plot(self):
        if self.data_queue:
            while len(self.data_queue) > 1:
                self.data_queue.popleft()
                
            samples = self.data_queue.popleft()
            fft_res = np.fft.rfft(samples * np.hanning(len(samples)))
            fft_db = 20 * np.log10(np.abs(fft_res) + 1e-9)
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
    root.geometry("1200x850")
    app = App(root)
    root.mainloop()
