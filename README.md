# Real-time Waveform Viewer

A desktop application for visualizing time-series waveform data from CSV files with interactive controls, written in Python using PyQt5 and pyqtgraph.

## 🎥 Demo

![Demo animation](demo.gif)

This animation demonstrates:

- Multiple synchronized waveform plots  
- Vertical time cursor and crosshair  
- Delta (Δ) measurement using Ctrl + click  
- Drag-and-drop plot reordering  
- Movable Y-value labels with lock behavior  

## 🔧 Features

- 📈 Plot CSV waveforms with time on the X-axis
- 🔀 Reorder plots via drag-and-drop
- 🎯 Time cursor synchronization across all plots
- 📌 Click to lock and track Y values
- 🧮 Δ (delta) measurement using two Ctrl+clicks
- 💾 Plot order saved between runs
- 🚀 Fast rendering enabled via Parquet caching

## 🚀 Pre-built Binaries

The application is also available as pre-compiled executables:

- 🪟 `bin/windows/plot.exe` – for Windows 11 or later
- 🐧 `bin/ubuntu/plot` – for Ubuntu Linux (x86_64)

You can run them directly without installing Python.

## 📂 Folder Structure

```
.
├── bin/
│   ├── windows/plot.exe  # Windows executable
│   └── ubuntu/plot       # Linux executable
├── plot.py               # Main application script
├── monitor_log.csv       # Input CSV file (time in first column)
├── plot_config.json      # Auto-generated config file
├── demo.gif              # Animation demo
└── README.md             # This file
```

## 📦 Requirements

Install the following Python packages:

```bash
pip install pyqt5 pyqtgraph pandas numpy pyarrow
```

## ▶ How to Run

```bash
python3 -u plot.py
```

Make sure your CSV file is named `monitor_log.csv` and is in the same directory.  
Each column should represent a variable. The first column must be time in seconds.

## 💾 Configuration and Cache

- `plot_config.json` is auto-generated to store:
  - Plot order
  - Cursor time
  - X-axis range

- `tool-temp/` is also created automatically:
  - Contains `monitor_log.parquet` (converted from CSV)
  - Significantly improves loading speed after first run

> 🧹 If plotting becomes unstable or incorrect, try deleting the `tool-temp/` folder and `plot_config.json` before restarting the app.

## 🛠 Future Plans

- Save/restore Δ measurement markers  

---

© 2025 misawa-san. Licensed under MIT.