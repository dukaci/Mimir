#!/usr/bin/env python3
"""
Mimir - Real-time CPU Process Monitor (DearPyGui Version)
GPU-accelerated, multi-threaded process monitoring with grouping support.
"""

import dearpygui.dearpygui as dpg
import psutil
import time
import threading
from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import configparser
import os
import ctypes
import sys

# Try to import pydivert for per-process network monitoring
try:
    import pydivert
    PYDIVERT_AVAILABLE = True
except ImportError:
    PYDIVERT_AVAILABLE = False

def is_admin():
    """Check if running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """Restart the application with administrator privileges."""
    try:
        if not is_admin():
            # Re-run the program with admin rights
            ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",  # Request elevation
                sys.executable,  # Python executable
                " ".join(sys.argv),  # Script + args
                None,
                1  # SW_SHOWNORMAL
            )
            # Forcefully exit - DearPyGui cleanup happens in the render loop
            os._exit(0)
    except Exception as e:
        print(f"Failed to elevate privileges: {e}")


def format_bytes(bytes_val):
    """Format bytes into human-readable string with appropriate units."""
    if bytes_val < 1024:
        return f"{bytes_val:.0f} B/s"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val/1024:.1f} KB/s"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val/(1024*1024):.1f} MB/s"
    else:
        return f"{bytes_val/(1024*1024*1024):.2f} GB/s"


class CPUMonitorDPG:
    def __init__(self):
        # Configuration
        self.config = self.load_config()
        self.sample_interval = self.config.getfloat('monitor', 'sample_interval', fallback=1.0)
        self.history_length = self.config.getint('monitor', 'history_length', fallback=300)
        self.top_processes = self.config.getint('display', 'top_processes', fallback=10)

        # Data storage
        self.history = deque(maxlen=self.history_length)
        self.process_names = {}  # pid -> name mapping
        self.cpu_count = psutil.cpu_count(logical=True)

        # Network monitoring with psutil IO counters
        self.network_stats = {}  # pid -> {'bytes_sent': int, 'bytes_recv': int}
        self.network_lock = threading.Lock()
        self.network_monitoring_thread = None

        # Threading
        self.running = False
        self.data_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=4)
        self.sampling_thread = None

        # Selection state (persistent, never cleared by UI updates)
        self.selected_process_pid = None
        self.selected_process_group = None  # List of PIDs
        self.selected_group_name = None

        # UI settings (load from config)
        self.filter_idle = self.config.getboolean('display', 'filter_idle', fallback=True)
        self.group_processes = self.config.getboolean('display', 'group_processes', fallback=False)
        self.left_panel_width = self.config.getint('display', 'left_panel_width', fallback=500)
        self.table_row_limit = self.config.getint('display', 'table_row_limit', fallback=20)
        self.aggregation_window = self.config.getint('display', 'aggregation_window', fallback=60)  # seconds
        self.chart_max_seconds = self.config.getint('display', 'chart_max_seconds', fallback=300)  # seconds

        # Network monitoring settings
        self.network_monitoring_enabled = self.config.getboolean('network', 'enabled', fallback=True)
        self.view_mode = self.config.get('network', 'view_mode', fallback='cpu')  # cpu, network, both
        self.sort_by = self.config.get('display', 'sort_by', fallback='cpu')  # cpu, network_down, network_up
        self.is_admin = is_admin()
        self.pydivert_available = PYDIVERT_AVAILABLE

        # View mode
        self.stacked_view = True  # Start in stacked view mode showing all top processes

        # DearPyGui widget IDs
        self.table_id = None
        self.plot_id = None
        self.status_text_id = None
        self.info_text_id = None
        self.admin_status_id = None

    def load_config(self):
        """Load configuration from file."""
        config = configparser.ConfigParser()
        config_file = 'config.ini'

        if not os.path.exists(config_file):
            config['monitor'] = {
                'sample_interval': '1.0',
                'history_length': '300'
            }
            config['display'] = {
                'top_processes': '10',
                'cpu_threshold': '1.0',
                'filter_idle': 'True',
                'group_processes': 'False',
                'left_panel_width': '500',
                'table_row_limit': '20',
                'aggregation_window': '60',
                'chart_max_seconds': '300',
                'sort_by': 'cpu'
            }
            config['network'] = {
                'enabled': 'True',
                'view_mode': 'cpu'
            }
            with open(config_file, 'w') as f:
                config.write(f)
        else:
            config.read(config_file)

        return config

    def save_config(self):
        """Save current configuration to file."""
        self.config['monitor']['sample_interval'] = str(self.sample_interval)
        self.config['monitor']['history_length'] = str(self.history_length)
        self.config['display']['top_processes'] = str(self.top_processes)
        self.config['display']['filter_idle'] = str(self.filter_idle)
        self.config['display']['group_processes'] = str(self.group_processes)
        self.config['display']['table_row_limit'] = str(self.table_row_limit)
        self.config['display']['aggregation_window'] = str(self.aggregation_window)
        self.config['display']['chart_max_seconds'] = str(self.chart_max_seconds)
        self.config['display']['sort_by'] = str(self.sort_by)

        # Network settings
        if 'network' not in self.config:
            self.config['network'] = {}
        self.config['network']['enabled'] = str(self.network_monitoring_enabled)
        self.config['network']['view_mode'] = str(self.view_mode)

        # Save left panel width if window exists
        if dpg.does_item_exist("left_panel"):
            width = dpg.get_item_width("left_panel")
            if width > 0:
                self.config['display']['left_panel_width'] = str(width)

        with open('config.ini', 'w') as f:
            self.config.write(f)

    def connection_mapping_thread(self):
        """Build mapping of network connections to process IDs using psutil."""
        # This thread periodically updates mapping of (local_ip, local_port, remote_ip, remote_port) -> PID
        # using psutil.net_connections() which doesn't require WinDivert SOCKET layer
        try:
            if not PYDIVERT_AVAILABLE or not self.is_admin:
                return

            print("[OK] Starting connection mapping thread using psutil...")
            update_count = 0

            while self.running:
                try:
                    # Build connection mapping from psutil
                    new_mapping = {}

                    # Get all network connections with their PIDs
                    connections = psutil.net_connections(kind='inet')

                    for conn in connections:
                        try:
                            if conn.pid and conn.laddr and conn.raddr:
                                # Local address and port
                                local_ip = conn.laddr.ip
                                local_port = conn.laddr.port

                                # Remote address and port
                                remote_ip = conn.raddr.ip
                                remote_port = conn.raddr.port

                                # Create connection tuple (matches packet direction)
                                conn_tuple = (local_ip, local_port, remote_ip, remote_port)
                                new_mapping[conn_tuple] = conn.pid

                                # Also store reverse tuple for incoming packets
                                reverse_tuple = (remote_ip, remote_port, local_ip, local_port)
                                new_mapping[reverse_tuple] = conn.pid

                        except (AttributeError, ValueError):
                            # Skip connections without full address info
                            pass

                    # Update the shared mapping atomically
                    self.connection_to_pid = new_mapping

                    update_count += 1
                    if update_count == 1:
                        print(f"[OK] Connection mapping initialized: {len(new_mapping)} mappings")
                    elif update_count % 10 == 0:
                        print(f"[DEBUG] Connection mapping updated: {len(new_mapping)} mappings")

                except Exception as e:
                    if update_count <= 3:
                        print(f"[DEBUG] Connection mapping error: {e}")

                # Update every 2 seconds (balance between freshness and CPU usage)
                time.sleep(2)

        except Exception as e:
            print(f"[ERROR] Connection mapping thread error: {e}")

    def network_monitoring_loop(self):
        """Monitor network usage per process using WinDivert NETWORK layer + psutil connection mapping."""
        try:
            if not PYDIVERT_AVAILABLE:
                print("[ERROR] PyDivert not available - network monitoring disabled")
                return

            if not self.is_admin:
                print("[ERROR] Administrator privileges required for network monitoring")
                return

            print("Starting WinDivert per-process network monitoring...")

            # Start connection mapping thread for PID mapping (using psutil)
            self.connection_to_pid = {}
            mapping_thread = threading.Thread(target=self.connection_mapping_thread, daemon=True)
            mapping_thread.start()

            # Small delay to let connection mapping initialize
            time.sleep(1.0)

            # WinDivert filter: capture TCP and UDP packets
            filter_str = "tcp or udp"

            print("[OK] Starting NETWORK layer packet capture...")
            packet_count = 0
            matched_packet_count = 0

            with pydivert.WinDivert(filter_str) as w:
                print("[OK] WinDivert driver loaded - capturing packets for per-process stats")

                for packet in w:
                    packet_count += 1
                    if not self.running:
                        print("Stopping packet capture...")
                        break

                    try:
                        # Debug: Show packet structure for first few packets
                        if packet_count <= 3:
                            print(f"[DEBUG] Packet #{packet_count} attributes: ipv4={hasattr(packet, 'ipv4')}, tcp={hasattr(packet, 'tcp')}, udp={hasattr(packet, 'udp')}")

                        # Extract IP addresses (PyDivert 2.x API)
                        src_ip = None
                        dst_ip = None
                        if packet.ipv4:
                            src_ip = str(packet.ipv4.src_addr)
                            dst_ip = str(packet.ipv4.dst_addr)
                        elif packet.ipv6:
                            src_ip = str(packet.ipv6.src_addr)
                            dst_ip = str(packet.ipv6.dst_addr)

                        if not src_ip or not dst_ip:
                            continue  # Not an IP packet

                        # Extract ports (PyDivert 2.x API)
                        src_port = 0
                        dst_port = 0
                        if packet.tcp:
                            src_port = packet.tcp.src_port
                            dst_port = packet.tcp.dst_port
                        elif packet.udp:
                            src_port = packet.udp.src_port
                            dst_port = packet.udp.dst_port
                        else:
                            continue  # Not TCP or UDP

                        # Try to match packet to PID using connection mapping
                        conn_tuple = (src_ip, src_port, dst_ip, dst_port)
                        pid = self.connection_to_pid.get(conn_tuple)

                        if pid and pid > 0:
                            matched_packet_count += 1
                            packet_size = len(packet.raw)

                            if matched_packet_count <= 5:
                                print(f"[DEBUG] Matched packet #{matched_packet_count}: PID {pid}, {packet_size} bytes, outbound={packet.is_outbound}")

                            with self.network_lock:
                                if pid not in self.network_stats:
                                    self.network_stats[pid] = {
                                        'bytes_sent': 0,
                                        'bytes_recv': 0,
                                        'last_reset': time.time()
                                    }

                                # Determine direction based on packet flow
                                if packet.is_outbound:
                                    self.network_stats[pid]['bytes_sent'] += packet_size
                                else:
                                    self.network_stats[pid]['bytes_recv'] += packet_size

                        elif packet_count % 1000 == 0:
                            print(f"[DEBUG] Packet stats: {packet_count} total, {matched_packet_count} matched, {len(self.connection_to_pid)} mappings")

                    except Exception as e:
                        # Silently ignore packet processing errors
                        # Uncomment for debugging: print(f"[DEBUG] Packet error: {e}")
                        pass

                    try:
                        # Re-inject packet to maintain normal network flow
                        w.send(packet)
                    except Exception:
                        # Ignore send errors
                        pass

                print("Packet capture stopped")

        except PermissionError:
            print("[ERROR] Access denied - need administrator privileges for network monitoring")
        except Exception as e:
            print(f"[ERROR] WinDivert error: {e}")
            import traceback
            traceback.print_exc()

    def process_batch(self, procs_batch, time_delta=1.0):
        """Process a batch of processes in parallel."""
        results = {}
        for p in procs_batch:
            try:
                pid = p.pid
                name = p.info['name'] if 'name' in p.info else p.name()
                cpu_percent = p.cpu_percent(interval=None)
                cpu_fraction = cpu_percent / (100.0 * self.cpu_count)

                # Network monitoring from WinDivert (requires admin + PyDivert)
                net_down_rate = 0
                net_up_rate = 0

                if self.network_monitoring_enabled and self.is_admin and self.pydivert_available:
                    with self.network_lock:
                        if pid in self.network_stats:
                            stats = self.network_stats[pid]
                            current_time = time.time()
                            time_elapsed = current_time - stats.get('last_reset', current_time)

                            if time_elapsed > 0:
                                # Calculate rates
                                net_down_rate = stats['bytes_recv'] / time_elapsed
                                net_up_rate = stats['bytes_sent'] / time_elapsed

                                # Reset counters for next interval
                                stats['bytes_recv'] = 0
                                stats['bytes_sent'] = 0
                                stats['last_reset'] = current_time

                if cpu_fraction > 0.001 or net_down_rate > 0 or net_up_rate > 0:
                    results[pid] = {
                        'name': name,
                        'cpu': cpu_fraction,
                        'net_down': net_down_rate,
                        'net_up': net_up_rate
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return results

    def sample_once(self):
        """Take one CPU sample with parallel processing."""
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

            # Second pass: parallel batch processing
            snapshot = {}
            batch_size = max(50, len(procs) // 4)
            batches = [procs[i:i + batch_size] for i in range(0, len(procs), batch_size)]

            futures = [self.executor.submit(self.process_batch, batch, self.sample_interval) for batch in batches]

            for future in futures:
                batch_results = future.result()
                snapshot.update(batch_results)
                for pid, data in batch_results.items():
                    self.process_names[pid] = data['name']

            snapshot['_timestamp'] = datetime.now()

            with self.data_lock:
                self.history.append(snapshot)
                # Clean up dead PIDs from process_names
                current_pids = set(snapshot.keys()) - {'_timestamp'}
                if len(self.process_names) > 1000:  # Cleanup threshold
                    dead_pids = set(self.process_names.keys()) - current_pids
                    for pid in list(dead_pids)[:100]:  # Remove 100 oldest dead PIDs
                        self.process_names.pop(pid, None)

            # Update status
            total_cpu = sum(data['cpu'] for pid, data in snapshot.items()
                           if pid != '_timestamp')
            dpg.set_value(self.status_text_id, f"Active | Total CPU: {total_cpu*100:.1f}%")

        except Exception as e:
            dpg.set_value(self.status_text_id, f"Error: {e}")

    def sampling_loop(self):
        """Continuous sampling loop."""
        while self.running:
            try:
                self.sample_once()
            except Exception as e:
                print(f"Sampling error: {e}")
                time.sleep(1)

    def start_sampling(self):
        """Start background sampling."""
        if not self.running:
            self.running = True
            self.sampling_thread = threading.Thread(target=self.sampling_loop, daemon=True)
            self.sampling_thread.start()

            # Start network monitoring if enabled
            print("\n" + "="*60)
            print("NETWORK MONITORING STATUS")
            print("="*60)
            print(f"Network monitoring enabled (config): {self.network_monitoring_enabled}")
            print(f"Administrator privileges: {self.is_admin}")
            print(f"PyDivert library available: {self.pydivert_available}")

            if self.network_monitoring_enabled and self.is_admin and self.pydivert_available:
                if not self.network_monitoring_thread or not self.network_monitoring_thread.is_alive():
                    print("\n[OK] Starting per-process network monitoring...")
                    self.network_monitoring_thread = threading.Thread(target=self.network_monitoring_loop, daemon=True)
                    self.network_monitoring_thread.start()
                    print("[OK] WinDivert (NETWORK layer) + psutil connection mapping ACTIVE")
                else:
                    print("\n[OK] Network monitoring thread already running")
            else:
                print("\n[DISABLED] Per-process network monitoring is OFF")
                print("\nRequirements not met:")
                if not self.network_monitoring_enabled:
                    print("  [X] Network monitoring disabled in config.ini")
                else:
                    print("  [OK] Network monitoring enabled in config")
                if not self.is_admin:
                    print("  [X] Not running with Administrator privileges")
                    print("       -> Use run_admin.bat or right-click run.bat and select 'Run as administrator'")
                else:
                    print("  [OK] Running with Administrator privileges")
                if not self.pydivert_available:
                    print("  [X] PyDivert library not installed")
                    print("       -> Run setup.bat to install it")
                else:
                    print("  [OK] PyDivert library available")
            print("="*60 + "\n")

            dpg.configure_item("start_stop_btn", label="Pause")

    def stop_sampling(self):
        """Stop background sampling."""
        self.running = False
        if self.sampling_thread:
            self.sampling_thread.join(timeout=2)

        # Stop network monitoring
        if self.network_monitoring_thread and self.network_monitoring_thread.is_alive():
            self.network_monitoring_thread.join(timeout=2)

        dpg.configure_item("start_stop_btn", label="Resume")

    def toggle_sampling(self):
        """Toggle sampling on/off."""
        if self.running:
            self.stop_sampling()
        else:
            self.start_sampling()

    def clear_data(self):
        """Clear all data."""
        with self.data_lock:
            self.history.clear()
        self.process_names.clear()
        with self.network_lock:
            self.network_stats.clear()
        self.selected_process_pid = None
        self.selected_process_group = None
        self.selected_group_name = None

    def get_aggregation_snapshots(self):
        """Get snapshots within aggregation window."""
        with self.data_lock:
            if len(self.history) < 1:
                return []

            current_time = datetime.now()
            cutoff_time = current_time - timedelta(seconds=self.aggregation_window)

            snapshots = list(self.history)
            agg_snapshots = []

            for snap in reversed(snapshots):
                snap_time = snap.get('_timestamp', current_time)
                if snap_time >= cutoff_time:
                    agg_snapshots.insert(0, snap)
                else:
                    break

            return agg_snapshots

    def compute_rankings(self):
        """Compute process rankings for current aggregation window."""
        view_snapshots = self.get_aggregation_snapshots()

        if not view_snapshots:
            return []

        # Find all PIDs
        all_pids = set()
        for snap in view_snapshots:
            for key in snap:
                if key != '_timestamp' and isinstance(key, int):
                    all_pids.add(key)

        # Filter idle process
        if self.filter_idle:
            all_pids = {pid for pid in all_pids
                       if self.process_names.get(pid, '').lower() != 'system idle process'}

        total_snapshots = len(view_snapshots)

        # Calculate stats
        pid_stats = {}
        for pid in all_pids:
            total_cpu = 0
            max_cpu = 0
            total_net_down = 0
            total_net_up = 0
            max_net_down = 0
            max_net_up = 0

            for snap in view_snapshots:
                if pid in snap:
                    cpu = snap[pid].get('cpu', 0)
                    net_down = snap[pid].get('net_down', 0)
                    net_up = snap[pid].get('net_up', 0)

                    total_cpu += cpu
                    max_cpu = max(max_cpu, cpu)
                    total_net_down += net_down
                    total_net_up += net_up
                    max_net_down = max(max_net_down, net_down)
                    max_net_up = max(max_net_up, net_up)

            pid_stats[pid] = {
                'avg': total_cpu / total_snapshots,
                'max': max_cpu,
                'avg_net_down': total_net_down / total_snapshots,
                'avg_net_up': total_net_up / total_snapshots,
                'max_net_down': max_net_down,
                'max_net_up': max_net_up,
                'name': self.process_names.get(pid, f"PID {pid}")
            }

        # Group if enabled
        if self.group_processes:
            grouped_stats = {}
            for pid, stats in pid_stats.items():
                name = stats['name']
                if name not in grouped_stats:
                    grouped_stats[name] = {
                        'pids': [],
                        'total_avg': 0,
                        'total_max': 0,
                        'total_net_down': 0,
                        'total_net_up': 0,
                        'max_net_down': 0,
                        'max_net_up': 0
                    }
                grouped_stats[name]['pids'].append(pid)
                grouped_stats[name]['total_avg'] += stats['avg']
                grouped_stats[name]['total_max'] = max(grouped_stats[name]['total_max'], stats['max'])
                grouped_stats[name]['total_net_down'] += stats['avg_net_down']
                grouped_stats[name]['total_net_up'] += stats['avg_net_up']
                grouped_stats[name]['max_net_down'] = max(grouped_stats[name]['max_net_down'], stats['max_net_down'])
                grouped_stats[name]['max_net_up'] = max(grouped_stats[name]['max_net_up'], stats['max_net_up'])

            # Determine sort key based on sort_by setting
            if self.sort_by == 'network_down':
                sort_key = lambda x: x[1]['total_net_down']
            elif self.sort_by == 'network_up':
                sort_key = lambda x: x[1]['total_net_up']
            else:  # Default to CPU
                sort_key = lambda x: x[1]['total_avg']

            # Convert to list with group info
            rankings = []
            for name, group_data in sorted(grouped_stats.items(),
                                          key=sort_key,
                                          reverse=True)[:self.table_row_limit]:
                rankings.append({
                    'type': 'group',
                    'name': name,
                    'pids': group_data['pids'],
                    'avg_cpu': group_data['total_avg'] * 100,
                    'max_cpu': group_data['total_max'] * 100,
                    'avg_net_down': group_data['total_net_down'],
                    'avg_net_up': group_data['total_net_up'],
                    'max_net_down': group_data['max_net_down'],
                    'max_net_up': group_data['max_net_up'],
                    'count': len(group_data['pids'])
                })
        else:
            # Individual processes
            # Determine sort key based on sort_by setting
            if self.sort_by == 'network_down':
                sort_key = lambda x: pid_stats[x]['avg_net_down']
            elif self.sort_by == 'network_up':
                sort_key = lambda x: pid_stats[x]['avg_net_up']
            else:  # Default to CPU
                sort_key = lambda x: pid_stats[x]['avg']

            rankings = []
            for pid in sorted(pid_stats.keys(),
                            key=sort_key,
                            reverse=True)[:self.table_row_limit]:
                rankings.append({
                    'type': 'pid',
                    'name': pid_stats[pid]['name'],
                    'pid': pid,
                    'avg_cpu': pid_stats[pid]['avg'] * 100,
                    'max_cpu': pid_stats[pid]['max'] * 100,
                    'avg_net_down': pid_stats[pid]['avg_net_down'],
                    'avg_net_up': pid_stats[pid]['avg_net_up'],
                    'max_net_down': pid_stats[pid]['max_net_down'],
                    'max_net_up': pid_stats[pid]['max_net_up']
                })

        return rankings

    def update_table(self):
        """Update process ranking table."""
        if self.table_id is None:
            return

        rankings = self.compute_rankings()

        # Clear only table rows (not columns)
        # Get all children and delete only table_row items
        if dpg.does_item_exist(self.table_id):
            children = dpg.get_item_children(self.table_id, slot=1)  # slot 1 is rows
            if children:
                for child in children:
                    dpg.delete_item(child)

        # Populate table
        for rank, entry in enumerate(rankings, 1):
            if entry['type'] == 'group':
                # Group row
                with dpg.table_row(parent=self.table_id):
                    dpg.add_text(f"{rank}")
                    if entry['count'] > 1:
                        dpg.add_text(f"{entry['name']} ({entry['count']} instances)")
                    else:
                        dpg.add_text(entry['name'])
                    dpg.add_text(f"{entry['avg_cpu']:.1f}%")
                    dpg.add_text(f"{entry['max_cpu']:.1f}%")
                    dpg.add_text(format_bytes(entry.get('avg_net_down', 0)))
                    dpg.add_text(format_bytes(entry.get('avg_net_up', 0)))
                    # Add button to select group
                    btn = dpg.add_button(label="View", width=70,
                                        callback=lambda s, a, u: self.select_group(u),
                                        user_data=entry)
            else:
                # Individual PID row
                with dpg.table_row(parent=self.table_id):
                    dpg.add_text(f"{rank}")
                    dpg.add_text(entry['name'])
                    dpg.add_text(f"{entry['avg_cpu']:.1f}%")
                    dpg.add_text(f"{entry['max_cpu']:.1f}%")
                    dpg.add_text(format_bytes(entry.get('avg_net_down', 0)))
                    dpg.add_text(format_bytes(entry.get('avg_net_up', 0)))
                    btn = dpg.add_button(label="View", width=70,
                                        callback=lambda s, a, u: self.select_pid(u),
                                        user_data=entry)

    def select_group(self, entry):
        """Select a process group."""
        self.stacked_view = False
        self.selected_process_pid = None
        self.selected_process_group = entry['pids']
        self.selected_group_name = entry['name']
        print(f"Selected group: {entry['name']} with {len(entry['pids'])} PIDs")

    def select_pid(self, entry):
        """Select individual process."""
        self.stacked_view = False
        self.selected_process_pid = entry['pid']
        self.selected_process_group = None
        self.selected_group_name = None
        print(f"Selected PID: {entry['pid']} ({entry['name']})")

    def reset_to_stacked_view(self):
        """Reset to stacked view showing all top processes."""
        self.stacked_view = True
        self.selected_process_pid = None
        self.selected_process_group = None
        self.selected_group_name = None
        print("Reset to stacked view - showing all top processes")


    def get_plot_data(self):
        """Get data for plotting based on current view mode."""
        with self.data_lock:
            if len(self.history) < 2:
                return None

            snapshots = list(self.history)

        # Filter snapshots to chart_max_seconds window
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(seconds=self.chart_max_seconds)

        filtered_snapshots = []
        for snap in reversed(snapshots):
            snap_time = snap.get('_timestamp', current_time)
            if snap_time >= cutoff_time:
                filtered_snapshots.insert(0, snap)
            else:
                break

        if len(filtered_snapshots) < 2:
            return None

        snapshots = filtered_snapshots
        timestamps = [snap.get('_timestamp', datetime.now()) for snap in snapshots]

        # Stacked view - show multiple processes/groups
        if self.stacked_view:
            rankings = self.compute_rankings()
            if not rankings:
                return None

            series_dict = {}

            for entry in rankings[:self.top_processes]:
                if entry['type'] == 'group':
                    name = entry['name']
                    if entry['count'] > 1:
                        name = f"{name} ({entry['count']})"

                    # CPU data
                    if self.view_mode in ['cpu', 'both']:
                        cpu_series = []
                        for snap in snapshots:
                            total_cpu = 0.0
                            for pid in entry['pids']:
                                if pid in snap:
                                    total_cpu += snap[pid].get('cpu', 0)
                            cpu_series.append(total_cpu * 100)
                        series_dict[f"{name} - CPU"] = cpu_series

                    # Network data
                    if self.view_mode in ['network', 'both']:
                        net_down_series = []
                        net_up_series = []
                        for snap in snapshots:
                            total_down = 0.0
                            total_up = 0.0
                            for pid in entry['pids']:
                                if pid in snap:
                                    total_down += snap[pid].get('net_down', 0)
                                    total_up += snap[pid].get('net_up', 0)
                            net_down_series.append(total_down / 1024)  # Convert to KB/s
                            net_up_series.append(total_up / 1024)  # Convert to KB/s
                        series_dict[f"{name} ↓"] = net_down_series
                        series_dict[f"{name} ↑"] = net_up_series

                else:
                    # Individual PID
                    name = entry['name']

                    # CPU data
                    if self.view_mode in ['cpu', 'both']:
                        cpu_series = []
                        for snap in snapshots:
                            if entry['pid'] in snap:
                                cpu_series.append(snap[entry['pid']].get('cpu', 0) * 100)
                            else:
                                cpu_series.append(0.0)
                        series_dict[f"{name} - CPU"] = cpu_series

                    # Network data
                    if self.view_mode in ['network', 'both']:
                        net_down_series = []
                        net_up_series = []
                        for snap in snapshots:
                            if entry['pid'] in snap:
                                net_down_series.append(snap[entry['pid']].get('net_down', 0) / 1024)
                                net_up_series.append(snap[entry['pid']].get('net_up', 0) / 1024)
                            else:
                                net_down_series.append(0.0)
                                net_up_series.append(0.0)
                        series_dict[f"{name} ↓"] = net_down_series
                        series_dict[f"{name} ↑"] = net_up_series

            return {'timestamps': timestamps, 'series': series_dict, 'mode': 'stacked', 'view_mode': self.view_mode}

        # Selected group
        if self.selected_process_group is not None:
            series_dict = {}

            if self.view_mode in ['cpu', 'both']:
                cpu_series = []
                for snap in snapshots:
                    total_cpu = 0.0
                    for pid in self.selected_process_group:
                        if pid in snap:
                            total_cpu += snap[pid].get('cpu', 0)
                    cpu_series.append(total_cpu * 100)
                series_dict['CPU'] = cpu_series

            if self.view_mode in ['network', 'both']:
                net_down_series = []
                net_up_series = []
                for snap in snapshots:
                    total_down = 0.0
                    total_up = 0.0
                    for pid in self.selected_process_group:
                        if pid in snap:
                            total_down += snap[pid].get('net_down', 0)
                            total_up += snap[pid].get('net_up', 0)
                    net_down_series.append(total_down / 1024)
                    net_up_series.append(total_up / 1024)
                series_dict['Download'] = net_down_series
                series_dict['Upload'] = net_up_series

            return {'timestamps': timestamps, 'series': series_dict, 'mode': 'single', 'view_mode': self.view_mode}

        # Selected individual PID
        if self.selected_process_pid is not None:
            series_dict = {}

            if self.view_mode in ['cpu', 'both']:
                cpu_series = []
                for snap in snapshots:
                    if self.selected_process_pid in snap:
                        cpu_series.append(snap[self.selected_process_pid].get('cpu', 0) * 100)
                    else:
                        cpu_series.append(0.0)
                series_dict['CPU'] = cpu_series

            if self.view_mode in ['network', 'both']:
                net_down_series = []
                net_up_series = []
                for snap in snapshots:
                    if self.selected_process_pid in snap:
                        net_down_series.append(snap[self.selected_process_pid].get('net_down', 0) / 1024)
                        net_up_series.append(snap[self.selected_process_pid].get('net_up', 0) / 1024)
                    else:
                        net_down_series.append(0.0)
                        net_up_series.append(0.0)
                series_dict['Download'] = net_down_series
                series_dict['Upload'] = net_up_series

            return {'timestamps': timestamps, 'series': series_dict, 'mode': 'single', 'view_mode': self.view_mode}

        return None

    def update_plot(self):
        """Update the CPU chart."""
        plot_data = self.get_plot_data()

        if plot_data is None:
            return

        timestamps = plot_data['timestamps']
        series_dict = plot_data['series']
        mode = plot_data['mode']

        if not timestamps or not series_dict:
            return

        # Convert timestamps to relative seconds
        start_time = timestamps[0]
        x_data = [(t - start_time).total_seconds() for t in timestamps]

        # Clear existing series except the y_axis
        if dpg.does_item_exist("y_axis"):
            children = dpg.get_item_children("y_axis", slot=1)
            if children:
                for child in children:
                    dpg.delete_item(child)

        # Color palette for stacked view
        colors = [
            (137, 180, 250),  # Blue
            (245, 194, 231),  # Pink
            (166, 227, 161),  # Green
            (249, 226, 175),  # Yellow
            (243, 139, 168),  # Red
            (148, 226, 213),  # Teal
            (203, 166, 247),  # Lavender
            (250, 179, 135),  # Peach
            (186, 194, 222),  # Overlay2
            (116, 199, 236),  # Sky
        ]

        # Add series
        all_y_values = []
        for idx, (name, y_data) in enumerate(series_dict.items()):
            color = colors[idx % len(colors)]

            # Create line series
            series_tag = f"plot_series_{idx}"
            if dpg.does_item_exist("y_axis"):
                dpg.add_line_series(x_data, y_data, label=name, parent="y_axis",
                                  tag=series_tag)
                dpg.bind_item_theme(series_tag, self._create_series_theme(color))

            all_y_values.extend(y_data)

        # Set axis limits
        if len(x_data) > 0 and all_y_values:
            x_min, x_max = min(x_data), max(x_data)
            y_max = max(max(all_y_values) * 1.1, 10)  # At least 10% headroom

            if dpg.does_item_exist("x_axis"):
                dpg.set_axis_limits("x_axis", x_min, x_max)
            if dpg.does_item_exist("y_axis"):
                dpg.set_axis_limits("y_axis", 0, y_max)

        # Update title based on selection and view mode
        view_mode = plot_data.get('view_mode', 'cpu')

        if view_mode == 'cpu':
            metric = "CPU Usage"
            y_label = "CPU %"
        elif view_mode == 'network':
            metric = "Network Usage"
            y_label = "KB/s"
        else:  # both
            metric = "CPU & Network"
            y_label = "Value"

        if mode == 'stacked':
            title = f"{metric}: Top {self.top_processes} Processes"
        elif self.selected_process_group:
            title = f"{metric}: {self.selected_group_name} ({len(self.selected_process_group)} instances)"
        elif self.selected_process_pid:
            name = self.process_names.get(self.selected_process_pid, f"PID {self.selected_process_pid}")
            title = f"{metric}: {name}"
        else:
            title = metric

        if dpg.does_item_exist("plot_title"):
            dpg.set_value("plot_title", title)

    def _create_series_theme(self, color):
        """Create a theme for a line series with the given color."""
        with dpg.theme() as theme:
            with dpg.theme_component(dpg.mvLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, color, category=dpg.mvThemeCat_Plots)
        return theme

    def update_ui(self):
        """Main UI update callback (called periodically)."""
        if self.running:
            self.update_table()
            self.update_plot()

    def setup_theme(self):
        """Setup modern dark theme."""
        with dpg.theme() as global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (30, 30, 46))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (49, 50, 68))
                dpg.add_theme_color(dpg.mvThemeCol_Border, (69, 71, 90))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (205, 214, 244))
                dpg.add_theme_color(dpg.mvThemeCol_Button, (137, 180, 250))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (116, 199, 236))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (148, 226, 213))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (49, 50, 68))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (88, 91, 112))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (69, 71, 90))
                dpg.add_theme_color(dpg.mvThemeCol_Header, (137, 180, 250, 79))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (137, 180, 250, 128))
                dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (137, 180, 250))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 10)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 8, 4)

        dpg.bind_theme(global_theme)

    def setup_gui(self):
        """Setup the DearPyGui interface."""
        dpg.create_context()

        # Main window
        with dpg.window(label="Mimir - CPU Process Monitor", tag="main_window"):
            # Control panel
            with dpg.group(horizontal=True):
                self.status_text_id = dpg.add_text("Initializing...")
                dpg.add_button(label="Pause", tag="start_stop_btn",
                             callback=self.toggle_sampling)
                dpg.add_button(label="Clear Data", callback=self.clear_data)
                dpg.add_button(label="Stacked View", callback=self.reset_to_stacked_view)

                # Network monitoring status
                if self.network_monitoring_enabled and self.is_admin and self.pydivert_available:
                    status_text = "[ACTIVE] Per-Process Network"
                    status_color = (148, 226, 213)
                elif not self.is_admin:
                    status_text = "[NEED ADMIN] Network Disabled"
                    status_color = (249, 226, 175)
                elif not self.pydivert_available:
                    status_text = "[NEED PyDivert] Network Disabled"
                    status_color = (249, 226, 175)
                else:
                    status_text = "[OFF] Network Disabled"
                    status_color = (249, 226, 175)

                self.admin_status_id = dpg.add_text(status_text, color=status_color)

                # Add button to enable network monitoring if not admin
                if not self.is_admin and self.network_monitoring_enabled:
                    dpg.add_button(label="Enable Network (Restart as Admin)",
                                 callback=run_as_admin,
                                 tag="admin_restart_btn")

            dpg.add_separator()

            # Settings Row 1
            with dpg.group(horizontal=True):
                dpg.add_text("Sample Interval (s):")
                dpg.add_input_float(default_value=self.sample_interval, width=80,
                                   callback=lambda s, v: setattr(self, 'sample_interval', v))

                dpg.add_text("Top Processes:")
                dpg.add_input_int(default_value=self.top_processes, width=80,
                                 callback=lambda s, v: setattr(self, 'top_processes', v))

                dpg.add_text("Table Rows:")
                dpg.add_input_int(default_value=self.table_row_limit, width=80,
                                 callback=lambda s, v: setattr(self, 'table_row_limit', max(1, v)))

                dpg.add_text("Avg Window (s):")
                dpg.add_input_int(default_value=self.aggregation_window, width=80,
                                 callback=lambda s, v: setattr(self, 'aggregation_window', max(1, v)))

                dpg.add_text("Chart X-Axis (s):")
                dpg.add_input_int(default_value=self.chart_max_seconds, width=80,
                                 callback=lambda s, v: setattr(self, 'chart_max_seconds', max(1, v)))

                dpg.add_checkbox(label="Filter Idle", default_value=self.filter_idle,
                                callback=lambda s, v: setattr(self, 'filter_idle', v))

                dpg.add_checkbox(label="Group Processes", default_value=self.group_processes,
                                callback=lambda s, v: setattr(self, 'group_processes', v))

            # Settings Row 2 - View and Network Controls
            with dpg.group(horizontal=True):
                dpg.add_text("View Mode:")
                dpg.add_combo(items=["cpu", "network", "both"],
                            default_value=self.view_mode,
                            width=100,
                            callback=lambda s, v: setattr(self, 'view_mode', v),
                            tag="view_mode_combo")

                dpg.add_text("Sort By:")
                dpg.add_combo(items=["cpu", "network_down", "network_up"],
                            default_value=self.sort_by,
                            width=120,
                            callback=lambda s, v: setattr(self, 'sort_by', v),
                            tag="sort_by_combo")

                dpg.add_checkbox(label="Network Monitoring",
                                default_value=self.network_monitoring_enabled,
                                callback=lambda s, v: setattr(self, 'network_monitoring_enabled', v),
                                tag="network_monitoring_checkbox")

            dpg.add_separator()

            # Main content area
            with dpg.group(horizontal=True):
                # Left panel - Rankings table
                with dpg.child_window(width=self.left_panel_width, height=-1, tag="left_panel"):
                    dpg.add_text("📊 Process Rankings", color=(137, 180, 250))

                    with dpg.table(header_row=True, borders_innerH=True,
                                 borders_outerH=True, borders_innerV=True,
                                 borders_outerV=True, row_background=True,
                                 resizable=True, policy=dpg.mvTable_SizingStretchProp) as self.table_id:
                        dpg.add_table_column(label="Rank", init_width_or_weight=0.4)
                        dpg.add_table_column(label="Process Name", init_width_or_weight=1.8)
                        dpg.add_table_column(label="Avg CPU %", init_width_or_weight=0.7)
                        dpg.add_table_column(label="Max CPU %", init_width_or_weight=0.7)
                        dpg.add_table_column(label="Net ↓", init_width_or_weight=0.8)
                        dpg.add_table_column(label="Net ↑", init_width_or_weight=0.8)
                        dpg.add_table_column(label="Action", init_width_or_weight=0.8)

                # Right panel - Chart
                with dpg.child_window(width=-1, height=-1, tag="right_panel"):
                    dpg.add_text("Chart", tag="plot_title", color=(137, 180, 250))

                    with dpg.plot(label="CPU Usage Chart", height=-1, width=-1, tag="cpu_plot"):
                        dpg.add_plot_legend()
                        dpg.add_plot_axis(dpg.mvXAxis, label="Time (s)", tag="x_axis")
                        dpg.add_plot_axis(dpg.mvYAxis, label="CPU %", tag="y_axis")

        # Setup theme
        self.setup_theme()

        # Setup viewport
        dpg.create_viewport(title="Mimir - CPU Monitor", width=1400, height=850)

        # Configure .ini file for settings persistence
        dpg.configure_app(init_file="mimir_layout.ini")

        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_window", True)

        # Start sampling
        self.start_sampling()

        # Track last update time for throttling
        self.last_table_update = 0
        self.last_chart_update = 0
        self.table_update_interval = 2.0  # seconds
        self.chart_update_interval = 0.5  # seconds (faster for smooth charts)

    def run(self):
        """Run the application."""
        self.setup_gui()

        # Main render loop with periodic UI updates
        import time as time_module
        while dpg.is_dearpygui_running():
            if self.running:
                current_time = time_module.time()

                # Update table every 2 seconds
                if current_time - self.last_table_update >= self.table_update_interval:
                    self.update_table()
                    self.last_table_update = current_time

                # Update chart more frequently (every 0.5 seconds) for smooth animation
                if current_time - self.last_chart_update >= self.chart_update_interval:
                    self.update_plot()
                    self.last_chart_update = current_time

            # Render frame (60 FPS)
            dpg.render_dearpygui_frame()

        # Cleanup
        self.stop_sampling()
        self.save_config()  # Save settings before closing
        self.executor.shutdown(wait=False)
        dpg.destroy_context()


def main():
    """Main entry point."""
    app = CPUMonitorDPG()
    app.run()


if __name__ == "__main__":
    main()
