# Rigol Instrument Control — PyVISA + PyQt5

![Python](https://img.shields.io/badge/Python-3.14-blue)
![PyVISA](https://img.shields.io/badge/PyVISA-1.14-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

Real-time instrument control GUI for **Rigol DG4162 Function Generator** and **Rigol DS2202A Oscilloscope** via USB-VISA.
Built as Project N1 for MSc Measurement & Sensor Technology — Hochschule Coburg.

## Demo

![GUI Screenshot](screenshots/gui_main.png)
*Live sine wave capture: 37 Hz, 4.50 Vpp | Rigol DS2202A via PyVISA*

## Features

- Live oscilloscope waveform display (real-time binary data via SCPI)
- Full function generator control: frequency, amplitude, waveform, offset, phase
- Automated Bode Plotter (frequency sweep with auto timebase)
- Modulation support: AM, FM
- Burst mode and polarity control
- Auto-logging to CSV with configurable interval
- Screenshot export
- Hardware parameter readout console
- QThread-based architecture — GUI never freezes

## Hardware

| Device | Model | Connection |
|--------|-------|------------|
| Function Generator | Rigol DG4162 (160 MHz) | USB-VISA |
| Oscilloscope | Rigol DS2202A (200 MHz) | USB-VISA |
| PC | Windows 11 | NI-VISA 2026 |

## Quick Start

```bash
pip install pyvisa pyqt5 matplotlib numpy
python main.py
```

Set your VISA addresses in main.py:
```python
SCOPE_VISA_ADDRESS = "USB0::0x1AB1::0x04B0::DS2D245104188::INSTR"
GEN_VISA_ADDRESS   = "USB0::0x1AB1::0x0641::DG4C145200907::INSTR"
```

## Requirements

```
pyvisa
pyvisa-py
PyQt5
matplotlib
numpy
```

Also requires: NI-VISA (download from ni.com/visa)

## Project Context
Siemens · NI/Emerson · ABB · Bosch · Rohde & Schwarz · Keysight


## License

MIT License — free to use and modify
