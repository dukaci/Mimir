#!/usr/bin/env python3
"""
CPU Monitor - Real-time per-process CPU usage visualization
Creates a stacked area chart showing CPU usage over time for all processes.
"""

import psutil
import time
import threading
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from collections import defaultdict, deque
import configparser
import sys
import os
from datetime import datetime


class CPUMonitor:
    def __init__(self, config_file='config.ini'):
        self.config = self.load_config(config_file)

        # Configuration
        self.sample_interval = self.config.getfloat('monitor', 'sample_interval', fallback=1.0)
        self.history_length = self.config.getint('monitor', 'history_length', fallback=300)
        self.top_processes = self.config.getint('display', 'top_processes', fallback=10)
        self.cpu_threshold = self.config.getfloat('display', 'cpu_threshold', fallback=1.0)

        # Data storage
        self.history = deque(maxlen=self.history_length)
        self.process_names = {}  # pid -> name mapping
        self.running = False
        self.sampling_thread = None

        # Get CPU count for normalization
        self.cpu_count = psutil.cpu_count(logical=True)

        print(f"CPU Monitor initialized:")
        print(f"  Sample interval: {self.sample_interval}s")
        print(f"  History length: {self.history_length} samples")
        print(f"  CPU cores: {self.cpu_count}")
        print(f"  Showing top {self.top_processes} processes")

    def load_config(self, config_file):
        """Load configuration from file, create default if not exists."""
        config = configparser.ConfigParser()

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
            print(f"Created default config file: {config_file}")
        else:
            config.read(config_file)

        return config

    def sample_once(self):
        """Take one sample of CPU usage per process."""
        try:
            # First pass: initialize cpu_percent for all processes
            procs = []
            for p in psutil.process_iter(attrs=['pid', 'name']):
                try:
                    p.cpu_percent(interval=None)  # Initialize
                    procs.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Wait the interval
            time.sleep(self.sample_interval)

            # Second pass: get actual CPU usage
            snapshot = {}
            for p in procs:
                try:
                    info = p.as_dict(attrs=['pid', 'name'])
                    cpu_percent = p.cpu_percent(interval=None)

                    # Normalize to fraction of total CPU (0.0 to 1.0)
                    cpu_fraction = cpu_percent / (100.0 * self.cpu_count)

                    if cpu_fraction > 0.001:  # Only include processes using CPU
                        snapshot[info['pid']] = {
                            'name': info['name'],
                            'cpu': cpu_fraction
                        }
                        self.process_names[info['pid']] = info['name']

                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            # Add timestamp
            snapshot['_timestamp'] = datetime.now()
            self.history.append(snapshot)

        except Exception as e:
            print(f"Error in sampling: {e}")

    def sampling_loop(self):
        """Continuous sampling loop."""
        print("Starting CPU sampling...")
        while self.running:
            try:
                self.sample_once()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Sampling error: {e}")
                time.sleep(1)

    def start_sampling(self):
        """Start the sampling thread."""
        if not self.running:
            self.running = True
            self.sampling_thread = threading.Thread(target=self.sampling_loop, daemon=True)
            self.sampling_thread.start()

    def stop_sampling(self):
        """Stop the sampling thread."""
        self.running = False
        if self.sampling_thread:
            self.sampling_thread.join(timeout=2)

    def get_plot_data(self):
        """Prepare data for plotting."""
        if len(self.history) < 2:
            return None, None, None

        # Convert history to list for easier processing
        snapshots = list(self.history)

        # Find all PIDs that appear in history
        all_pids = set()
        for snap in snapshots:
            for key in snap:
                if key != '_timestamp' and isinstance(key, int):
                    all_pids.add(key)

        if not all_pids:
            return None, None, None

        # Calculate average CPU usage for each process to find top ones
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

        # Select top processes by average CPU usage
        top_pids = sorted(pid_averages.keys(),
                         key=lambda x: pid_averages[x],
                         reverse=True)[:self.top_processes]

        # Build time series for selected processes
        pid_series = {}
        for pid in top_pids:
            series = []
            for snap in snapshots:
                if pid in snap:
                    series.append(snap[pid]['cpu'])
                else:
                    series.append(0.0)
            pid_series[pid] = series

        # Calculate "others" series (all remaining processes)
        others_series = []
        for i, snap in enumerate(snapshots):
            others_cpu = 0.0
            for pid, data in snap.items():
                if (pid != '_timestamp' and isinstance(pid, int) and
                    pid not in top_pids):
                    others_cpu += data['cpu']
            others_series.append(others_cpu)

        # Prepare data for stackplot
        data_series = []
        labels = []

        # Add top processes
        for pid in top_pids:
            data_series.append(pid_series[pid])
            name = self.process_names.get(pid, f"PID {pid}")
            avg_cpu = pid_averages[pid] * 100
            labels.append(f"{name} ({avg_cpu:.1f}%)")

        # Add others if significant
        if any(cpu > 0.01 for cpu in others_series):
            data_series.append(others_series)
            avg_others = sum(others_series) / len(others_series) * 100
            labels.append(f"Others ({avg_others:.1f}%)")

        # Time axis
        timestamps = [snap.get('_timestamp', datetime.now()) for snap in snapshots]

        return data_series, labels, timestamps

    def plot_static(self):
        """Create a static plot of current data."""
        data_series, labels, timestamps = self.get_plot_data()

        if data_series is None:
            print("No data to plot yet. Collecting samples...")
            return

        plt.figure(figsize=(14, 8))

        # Create time axis (sample numbers for simplicity)
        x_axis = list(range(len(timestamps)))

        # Create stacked area plot
        plt.stackplot(x_axis, *data_series, labels=labels, alpha=0.8)

        # Customize plot
        plt.xlabel('Time (samples)')
        plt.ylabel('CPU Usage (fraction of total)')
        plt.title('Per-Process CPU Usage Over Time')
        plt.legend(loc='upper left', bbox_to_anchor=(1.05, 1), fontsize='small')
        plt.grid(True, alpha=0.3)
        plt.ylim(0, 1)

        # Format x-axis with timestamps if available
        if timestamps and len(timestamps) > 1:
            step = max(1, len(timestamps) // 10)
            tick_positions = x_axis[::step]
            tick_labels = [timestamps[i].strftime('%H:%M:%S') for i in tick_positions]
            plt.xticks(tick_positions, tick_labels, rotation=45)

        plt.tight_layout()
        plt.show()

    def export_csv(self, filename=None):
        """Export current data to CSV."""
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'cpu_data_{timestamp}.csv'

        if not self.history:
            print("No data to export")
            return

        import csv

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

            # Data rows
            for snap in self.history:
                row = [snap.get('_timestamp', '').strftime('%Y-%m-%d %H:%M:%S')]
                for pid in sorted(all_pids):
                    if pid in snap:
                        row.append(snap[pid]['cpu'])
                    else:
                        row.append(0.0)
                writer.writerow(row)

        print(f"Data exported to {filename}")


def main():
    """Main function."""
    if len(sys.argv) > 1 and sys.argv[1] == '--help':
        print("""
CPU Monitor - Real-time process CPU usage visualization

Usage:
    python cpu_monitor.py [options]

Commands during execution:
    - Press Ctrl+C to stop sampling
    - Close the plot window to refresh with new data

Configuration:
    Edit config.ini to customize:
    - sample_interval: seconds between samples (default: 1.0)
    - history_length: number of samples to keep (default: 300)
    - top_processes: max processes to show individually (default: 10)
    - cpu_threshold: minimum CPU % to track (default: 1.0)
        """)
        return

    monitor = CPUMonitor()

    try:
        # Start sampling
        monitor.start_sampling()

        # Wait for some initial data
        print("Collecting initial data...")
        time.sleep(5)

        # Interactive loop
        while True:
            print("\nOptions:")
            print("1. Show current plot")
            print("2. Export to CSV")
            print("3. Show statistics")
            print("4. Quit")

            try:
                choice = input("Enter choice (1-4): ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if choice == '1':
                monitor.plot_static()
            elif choice == '2':
                monitor.export_csv()
            elif choice == '3':
                data_series, labels, timestamps = monitor.get_plot_data()
                if data_series:
                    print(f"\nStatistics ({len(timestamps)} samples):")
                    for i, label in enumerate(labels):
                        avg_cpu = sum(data_series[i]) / len(data_series[i]) * 100
                        max_cpu = max(data_series[i]) * 100
                        print(f"  {label}: avg={avg_cpu:.1f}%, max={max_cpu:.1f}%")
                else:
                    print("No data available yet")
            elif choice == '4':
                break
            else:
                print("Invalid choice")

    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping monitor...")
        monitor.stop_sampling()
        print("Done.")


if __name__ == "__main__":
    main()