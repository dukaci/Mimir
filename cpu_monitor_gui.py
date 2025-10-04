#!/usr/bin/env python3
"""
CPU Monitor GUI - Real-time per-process CPU usage visualization with tkinter GUI
Creates a window with embedded real-time updating stacked area chart.
"""

import psutil
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from collections import defaultdict, deque
import configparser
import sys
import os
from datetime import datetime
import csv


class CPUMonitorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CPU Monitor - Real-time Process Monitoring")
        self.root.geometry("1200x800")

        # Configuration
        self.config = self.load_config()
        self.sample_interval = self.config.getfloat('monitor', 'sample_interval', fallback=1.0)
        self.history_length = self.config.getint('monitor', 'history_length', fallback=300)
        self.top_processes = self.config.getint('display', 'top_processes', fallback=10)

        # Data storage
        self.history = deque(maxlen=self.history_length)
        self.process_names = {}
        self.running = False
        self.sampling_thread = None
        self.cpu_count = psutil.cpu_count(logical=True)

        # GUI setup
        self.setup_gui()
        self.setup_plot()

        # Start sampling automatically
        self.start_sampling()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def load_config(self):
        """Load configuration from file."""
        config = configparser.ConfigParser()
        config_file = 'config.ini'

        if not os.path.exists(config_file):
            # Create default config
            config['monitor'] = {
                'sample_interval': '1.0',
                'history_length': '300'
            }
            config['display'] = {
                'top_processes': '10',
                'cpu_threshold': '1.0'
            }

            with open(config_file, 'w') as f:
                config.write(f)
        else:
            config.read(config_file)

        return config

    def setup_gui(self):
        """Setup the GUI components."""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # Control panel
        control_frame = ttk.LabelFrame(main_frame, text="Controls", padding="5")
        control_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        # Status label
        self.status_var = tk.StringVar(value="Initializing...")
        status_label = ttk.Label(control_frame, textvariable=self.status_var)
        status_label.grid(row=0, column=0, padx=(0, 20))

        # Start/Stop button
        self.start_stop_var = tk.StringVar(value="Stop")
        self.start_stop_btn = ttk.Button(control_frame, textvariable=self.start_stop_var,
                                        command=self.toggle_sampling)
        self.start_stop_btn.grid(row=0, column=1, padx=5)

        # Export button
        export_btn = ttk.Button(control_frame, text="Export CSV",
                               command=self.export_csv)
        export_btn.grid(row=0, column=2, padx=5)

        # Clear button
        clear_btn = ttk.Button(control_frame, text="Clear Data",
                              command=self.clear_data)
        clear_btn.grid(row=0, column=3, padx=5)

        # Settings frame
        settings_frame = ttk.LabelFrame(control_frame, text="Settings", padding="5")
        settings_frame.grid(row=1, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=(10, 0))

        # Sample interval
        ttk.Label(settings_frame, text="Sample Interval (s):").grid(row=0, column=0, padx=5)
        self.interval_var = tk.StringVar(value=str(self.sample_interval))
        interval_spin = ttk.Spinbox(settings_frame, from_=0.1, to=10.0, increment=0.1,
                                   textvariable=self.interval_var, width=10,
                                   command=self.update_interval)
        interval_spin.grid(row=0, column=1, padx=5)

        # Top processes
        ttk.Label(settings_frame, text="Top Processes:").grid(row=0, column=2, padx=5)
        self.top_procs_var = tk.StringVar(value=str(self.top_processes))
        top_procs_spin = ttk.Spinbox(settings_frame, from_=1, to=20,
                                    textvariable=self.top_procs_var, width=10,
                                    command=self.update_top_processes)
        top_procs_spin.grid(row=0, column=3, padx=5)

        # Info panel
        info_frame = ttk.LabelFrame(main_frame, text="System Info", padding="5")
        info_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 10))

        # System info
        self.info_text = tk.Text(info_frame, width=30, height=20)
        info_scroll = ttk.Scrollbar(info_frame, orient="vertical", command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=info_scroll.set)

        self.info_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        info_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        info_frame.columnconfigure(0, weight=1)
        info_frame.rowconfigure(0, weight=1)

        # Update system info
        self.update_system_info()

        # Plot frame
        plot_frame = ttk.LabelFrame(main_frame, text="CPU Usage Chart", padding="5")
        plot_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S))
        plot_frame.columnconfigure(0, weight=1)
        plot_frame.rowconfigure(0, weight=1)

        self.plot_frame = plot_frame

    def setup_plot(self):
        """Setup the matplotlib plot."""
        # Create figure and axis
        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)

        # Create canvas
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Add toolbar
        toolbar_frame = ttk.Frame(self.plot_frame)
        toolbar_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        toolbar.update()

        # Initialize empty plot
        self.ax.set_xlabel('Time (samples)')
        self.ax.set_ylabel('CPU Usage (fraction of total)')
        self.ax.set_title('Per-Process CPU Usage Over Time')
        self.ax.set_ylim(0, 1)
        self.ax.grid(True, alpha=0.3)

        # Start animation
        self.ani = animation.FuncAnimation(self.fig, self.update_plot,
                                          interval=1000, blit=False, cache_frame_data=False)

    def update_system_info(self):
        """Update system information display."""
        info = []
        info.append(f"CPU Cores: {self.cpu_count}")
        info.append(f"CPU Usage: {psutil.cpu_percent():.1f}%")

        # Memory info
        mem = psutil.virtual_memory()
        info.append(f"Memory: {mem.percent:.1f}%")
        info.append(f"Available: {mem.available / (1024**3):.1f} GB")

        # Process count
        info.append(f"Processes: {len(psutil.pids())}")

        # Sampling info
        info.append("")
        info.append(f"Samples: {len(self.history)}")
        info.append(f"Interval: {self.sample_interval}s")
        info.append(f"History: {self.history_length}")

        # Top processes by current CPU
        try:
            top_current = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                try:
                    if p.info['cpu_percent'] > 1.0:
                        top_current.append((p.info['name'], p.info['cpu_percent']))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            top_current.sort(key=lambda x: x[1], reverse=True)
            info.append("")
            info.append("Current Top Processes:")
            for name, cpu in top_current[:5]:
                info.append(f"  {name}: {cpu:.1f}%")

        except Exception:
            pass

        # Update text widget
        self.info_text.delete(1.0, tk.END)
        self.info_text.insert(1.0, "\n".join(info))

        # Schedule next update
        self.root.after(2000, self.update_system_info)

    def sample_once(self):
        """Take one sample of CPU usage per process."""
        try:
            # First pass: initialize cpu_percent
            procs = []
            for p in psutil.process_iter(attrs=['pid', 'name']):
                try:
                    p.cpu_percent(interval=None)
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Wait
            time.sleep(self.sample_interval)

            # Second pass: get actual usage
            snapshot = {}
            for p in procs:
                try:
                    info = p.as_dict(attrs=['pid', 'name'])
                    cpu_percent = p.cpu_percent(interval=None)
                    cpu_fraction = cpu_percent / (100.0 * self.cpu_count)

                    if cpu_fraction > 0.001:
                        snapshot[info['pid']] = {
                            'name': info['name'],
                            'cpu': cpu_fraction
                        }
                        self.process_names[info['pid']] = info['name']

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            snapshot['_timestamp'] = datetime.now()
            self.history.append(snapshot)

            # Update status
            total_cpu = sum(data['cpu'] for pid, data in snapshot.items()
                           if pid != '_timestamp')
            self.status_var.set(f"Sampling... Total CPU: {total_cpu*100:.1f}%")

        except Exception as e:
            self.status_var.set(f"Error: {e}")

    def sampling_loop(self):
        """Continuous sampling loop."""
        while self.running:
            try:
                self.sample_once()
            except Exception as e:
                print(f"Sampling error: {e}")
                time.sleep(1)

    def start_sampling(self):
        """Start sampling thread."""
        if not self.running:
            self.running = True
            self.sampling_thread = threading.Thread(target=self.sampling_loop, daemon=True)
            self.sampling_thread.start()
            self.start_stop_var.set("Stop")
            self.status_var.set("Starting sampling...")

    def stop_sampling(self):
        """Stop sampling thread."""
        self.running = False
        if self.sampling_thread:
            self.sampling_thread.join(timeout=2)
        self.start_stop_var.set("Start")
        self.status_var.set("Stopped")

    def toggle_sampling(self):
        """Toggle sampling on/off."""
        if self.running:
            self.stop_sampling()
        else:
            self.start_sampling()

    def update_interval(self):
        """Update sampling interval."""
        try:
            self.sample_interval = float(self.interval_var.get())
        except ValueError:
            self.interval_var.set(str(self.sample_interval))

    def update_top_processes(self):
        """Update number of top processes to show."""
        try:
            self.top_processes = int(self.top_procs_var.get())
        except ValueError:
            self.top_procs_var.set(str(self.top_processes))

    def clear_data(self):
        """Clear all historical data."""
        self.history.clear()
        self.process_names.clear()
        self.status_var.set("Data cleared")

    def get_plot_data(self):
        """Prepare data for plotting."""
        if len(self.history) < 2:
            return None, None, None

        snapshots = list(self.history)

        # Find all PIDs
        all_pids = set()
        for snap in snapshots:
            for key in snap:
                if key != '_timestamp' and isinstance(key, int):
                    all_pids.add(key)

        if not all_pids:
            return None, None, None

        # Calculate averages
        pid_averages = {}
        for pid in all_pids:
            total_cpu = 0
            count = 0
            for snap in snapshots:
                if pid in snap:
                    total_cpu += snap[pid]['cpu']
                    count += 1
            if count > 0:
                pid_averages[pid] = total_cpu / count

        # Select top processes
        top_pids = sorted(pid_averages.keys(),
                         key=lambda x: pid_averages[x],
                         reverse=True)[:self.top_processes]

        # Build series
        pid_series = {}
        for pid in top_pids:
            series = []
            for snap in snapshots:
                if pid in snap:
                    series.append(snap[pid]['cpu'])
                else:
                    series.append(0.0)
            pid_series[pid] = series

        # Others series
        others_series = []
        for i, snap in enumerate(snapshots):
            others_cpu = 0.0
            for pid, data in snap.items():
                if (pid != '_timestamp' and isinstance(pid, int) and
                    pid not in top_pids):
                    others_cpu += data['cpu']
            others_series.append(others_cpu)

        # Prepare final data
        data_series = []
        labels = []

        for pid in top_pids:
            data_series.append(pid_series[pid])
            name = self.process_names.get(pid, f"PID {pid}")
            avg_cpu = pid_averages[pid] * 100
            labels.append(f"{name} ({avg_cpu:.1f}%)")

        if any(cpu > 0.01 for cpu in others_series):
            data_series.append(others_series)
            avg_others = sum(others_series) / len(others_series) * 100
            labels.append(f"Others ({avg_others:.1f}%)")

        timestamps = [snap.get('_timestamp', datetime.now()) for snap in snapshots]

        return data_series, labels, timestamps

    def update_plot(self, frame):
        """Update the plot with current data."""
        data_series, labels, timestamps = self.get_plot_data()

        if data_series is None:
            return

        # Clear and redraw
        self.ax.clear()

        x_axis = list(range(len(timestamps)))

        # Create stacked plot
        self.ax.stackplot(x_axis, *data_series, labels=labels, alpha=0.8)

        # Customize
        self.ax.set_xlabel('Time (samples)')
        self.ax.set_ylabel('CPU Usage (fraction of total)')
        self.ax.set_title('Per-Process CPU Usage Over Time')
        self.ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize='small')
        self.ax.grid(True, alpha=0.3)
        self.ax.set_ylim(0, 1)

        # Format x-axis
        if timestamps and len(timestamps) > 1:
            step = max(1, len(timestamps) // 10)
            tick_positions = x_axis[::step]
            tick_labels = [timestamps[i].strftime('%H:%M:%S') for i in tick_positions]
            self.ax.set_xticks(tick_positions)
            self.ax.set_xticklabels(tick_labels, rotation=45)

        self.fig.tight_layout()

    def export_csv(self):
        """Export data to CSV file."""
        if not self.history:
            messagebox.showwarning("No Data", "No data to export")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export CPU Data"
        )

        if not filename:
            return

        try:
            with open(filename, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)

                # Header
                header = ['timestamp']
                all_pids = set()
                for snap in self.history:
                    for key in snap:
                        if key != '_timestamp' and isinstance(key, int):
                            all_pids.add(key)

                for pid in sorted(all_pids):
                    name = self.process_names.get(pid, f"PID_{pid}")
                    header.append(f"{name}_{pid}")
                writer.writerow(header)

                # Data
                for snap in self.history:
                    row = [snap.get('_timestamp', '').strftime('%Y-%m-%d %H:%M:%S')]
                    for pid in sorted(all_pids):
                        if pid in snap:
                            row.append(snap[pid]['cpu'])
                        else:
                            row.append(0.0)
                    writer.writerow(row)

            messagebox.showinfo("Export Complete", f"Data exported to {filename}")

        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export: {e}")

    def on_closing(self):
        """Handle window closing."""
        self.stop_sampling()
        self.root.destroy()


def main():
    """Main function."""
    root = tk.Tk()
    app = CPUMonitorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()