# CPU Monitor

A real-time CPU monitoring tool that visualizes per-process CPU usage with stacked area charts. Built for Windows with Python.

## Features

- **Real-time monitoring**: Continuous sampling of per-process CPU usage
- **Stacked area charts**: Visual representation of CPU usage over time
- **GUI and CLI versions**: Choose between graphical interface or command line
- **Smart process grouping**: Shows top N processes individually, groups others
- **Data export**: Export historical data to CSV format
- **Configurable**: Adjust sampling rate, history length, and display options
- **Windows optimized**: Handles Windows process permissions correctly

## Quick Start

### Option 1: One-Click Launch (Recommended)
1. Double-click `Run_mimir.bat` - automatically sets up and launches the GUI

### Option 2: Manual Setup
1. Double-click `setup.bat` to install dependencies
2. Double-click `Run_mimir.bat` to start the GUI
3. Or use `choose_version.bat` to select CLI/GUI/Tests

### Option 3: Manual Command Line
```cmd
# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run GUI version
python cpu_monitor_gui.py

# Or run CLI version
python cpu_monitor.py
```

## Files

### Main Scripts
- `cpu_monitor_gui.py` - GUI version with real-time charts and controls
- `cpu_monitor.py` - Command line version with interactive menu
- `config.ini` - Configuration file for sampling and display settings
- `requirements.txt` - Python package dependencies

### Launcher Scripts
- `Run_mimir.bat` - **One-click launcher** (auto-setup + GUI)
- `setup.bat` - Setup script (creates venv, installs dependencies)
- `choose_version.bat` - Interactive menu to choose GUI/CLI/Tests

### Test Scripts
- `test_without_deps.py` - Dependency-free functionality test

## Configuration

Edit `config.ini` to customize:

```ini
[monitor]
sample_interval = 1.0    # Seconds between samples
history_length = 300     # Number of samples to keep

[display]
top_processes = 10       # Max processes to show individually
cpu_threshold = 1.0      # Minimum CPU % to track
```

## GUI Features

- **Real-time chart**: Updates automatically with live data
- **System information**: Shows CPU, memory, and process statistics
- **Interactive controls**: Start/stop sampling, adjust settings
- **Export functionality**: Save data to CSV files
- **Navigation toolbar**: Zoom, pan, and save chart images

## Requirements

- Windows 10/11
- Python 3.8 or higher
- psutil (for system monitoring)
- matplotlib (for charting)
- tkinter (included with Python)

## Usage Tips

1. **Start with GUI version** - It's more user-friendly and shows real-time updates
2. **Adjust sample interval** - Lower values (0.5s) for more detail, higher (2s) for longer history
3. **Monitor system load** - The tool itself uses minimal CPU (typically <1%)
4. **Export data** - Use CSV export for analysis in Excel or other tools
5. **Admin privileges** - Run as administrator to monitor all system processes

## Troubleshooting

**"Permission denied" errors**: Run as administrator to access all processes

**High memory usage**: Reduce `history_length` in config.ini

**Slow updates**: Increase `sample_interval` or reduce `top_processes`

**Missing processes**: Some system processes require administrator privileges

## Examples

### Monitoring Development Workload
- Set `sample_interval = 0.5` for detailed monitoring
- Set `top_processes = 15` to see more applications
- Perfect for tracking compiler, IDE, and browser usage

### Long-term System Monitoring
- Set `sample_interval = 5.0` for longer sampling
- Set `history_length = 720` (1 hour at 5s intervals)
- Export data periodically for analysis

### Gaming Performance
- Set `sample_interval = 0.2` for high-resolution monitoring
- Monitor game, Discord, streaming software simultaneously
- Use CSV export to analyze performance patterns

## License

This is a prototype tool for educational and personal use. Feel free to modify and extend as needed.