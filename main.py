# -*- coding: utf-8 -*-
"""
Created on Thu Apr  2 19:43:05 2026

@author: pkhan
"""

import sys
import numpy as np
import pyvisa
from PyQt5 import QtWidgets, uic
from PyQt5.QtWidgets import QApplication, QFileDialog
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar 
from matplotlib.figure import Figure

# --- IMPORT BACKGROUND THREADS FROM OUR NEW FILE ---
from hardware_threads import HardwareWorker, BodeWorker

SCOPE_VISA_ADDRESS = "USB0::0x1AB1::0x04B0::DS2D245104188::INSTR" 
GEN_VISA_ADDRESS = "USB0::0x1AB1::0x0641::DG4C145200907::INSTR" 

class LabGUI(QtWidgets.QMainWindow):
    def __init__(self):
        super(LabGUI, self).__init__()
        
        # Load the Qt Designer UI file dynamically
        uic.loadUi('instrument_gui.ui', self)
        
        # Matplotlib Graph Setup
        self.figure = Figure()
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        layout = QtWidgets.QVBoxLayout(self.plot_container)
        layout.addWidget(self.toolbar) 
        layout.addWidget(self.canvas)  
        
        self.current_channel_data = {} 
        self.rm = pyvisa.ResourceManager("C:\\Windows\\System32\\visa64.dll")
        self.scope = None
        self.gen = None
        self.worker = None
        
        # Connect Buttons
        self.run_btn.clicked.connect(self.toggle_capture)
        if hasattr(self, 'run_bode_btn'): self.run_bode_btn.clicked.connect(self.run_bode_sweep) 
        self.save_img_btn.clicked.connect(self.save_image)
        self.clear_btn.clicked.connect(self.clear_plot)
        if hasattr(self, 'read_param_btn'): self.read_param_btn.clicked.connect(self.read_hardware_params)
            
        if hasattr(self, 'gen_out_btn'):
            self.gen_out_btn.clicked.connect(self.toggle_gen_output)
            if self.gen_out_btn.isChecked():
                self.gen_out_btn.setStyleSheet("background-color: #00aa00; font-weight: bold; color: white;")
            else:
                self.gen_out_btn.setStyleSheet("background-color: #aa0000; font-weight: bold; color: white;")
            
        self.clear_plot() 
        self.connect_instruments()

    def connect_instruments(self):
        self.status_label.setText("Status: Connecting...")
        self.status_label.setStyleSheet("color: orange;")
        QApplication.processEvents() 
        try:
            self.scope = self.rm.open_resource(SCOPE_VISA_ADDRESS)
            self.gen = self.rm.open_resource(GEN_VISA_ADDRESS)
            self.scope.timeout = 10000
            self.status_label.setText("Status: Connected!")
            self.status_label.setStyleSheet("color: #00FF00;") 
        except Exception as e:
            self.status_label.setText("Status: Connection Error!")
            self.status_label.setStyleSheet("color: red;")

    def toggle_gen_output(self):
        if not self.gen: return
        try:
            if self.gen_out_btn.isChecked():
                self.gen.write(":OUTPut1:STATe ON")
                self.gen_out_btn.setText("Gen Output: ON")
                self.gen_out_btn.setStyleSheet("background-color: #00aa00; font-weight: bold; color: white;") 
            else:
                self.gen.write(":OUTPut1:STATe OFF")
                self.gen_out_btn.setText("Gen Output: OFF")
                self.gen_out_btn.setStyleSheet("background-color: #aa0000; font-weight: bold; color: white;") 
        except Exception as e:
            if hasattr(self, 'status_label'): self.status_label.setText("Status: Gen Toggle Error!")
            print(f"Error toggling generator: {e}")

    def read_hardware_params(self):
        if not self.scope or not self.gen or (self.worker and self.worker.isRunning()): return
        try:
            if hasattr(self, 'info_console'): self.info_console.setPlainText("Querying instruments... Please wait.")
            QApplication.processEvents()

            gen_idn = self.gen.query("*IDN?").strip()
            gen_apply = self.gen.query(":SOURce1:APPLy?").strip()
            gen_imp = self.gen.query(":OUTPut1:IMPedance?").strip()
            gen_out = self.gen.query(":OUTPut1:STATe?").strip()

            scope_idn = self.scope.query("*IDN?").strip()
            time_scale = self.scope.query(":TIMebase:MAIN:SCALe?").strip()
            v_scale_ch1 = self.scope.query(":CHANnel1:SCALe?").strip()
            offset_ch1 = self.scope.query(":CHANnel1:OFFSet?").strip()
            trig_level = self.scope.query(":TRIGger:EDGe:LEVel?").strip()

            info = f"=== GENERATOR ===\nModel: {gen_idn}\nOutput: {'ON' if '1' in gen_out or 'ON' in gen_out else 'OFF'}\nImpedance: {gen_imp} Ω\nWaveform: {gen_apply}\n\n"
            info += f"=== OSCILLOSCOPE ===\nModel: {scope_idn}\nTime/Div: {time_scale} s\nCH1 Volts/Div: {v_scale_ch1} V\nCH1 Offset: {offset_ch1} V\nTrigger Level: {trig_level} V"

            if hasattr(self, 'info_console'): self.info_console.setPlainText(info)
        except Exception as e:
            if hasattr(self, 'info_console'): self.info_console.setPlainText(f"Error: {str(e)}")

    def toggle_capture(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.run_btn.setText("Stopping..."); return
        if not self.scope or not self.gen: return

        self.run_btn.setText("Stop Capture")
        self.run_btn.setStyleSheet("background-color: #aa0000; font-weight: bold; color: white;")
        self.progress_bar.setValue(0) 
        
        self.current_params = {
            'shape': getattr(self, 'shape_combo_2', type('obj', (object,), {'currentText': lambda: 'SINusoid'})).currentText(),
            'freq': getattr(self, 'freq_box', type('obj', (object,), {'value': lambda: 1000.0})).value(),
            'amp': getattr(self, 'amp_box', type('obj', (object,), {'value': lambda: 2.0})).value(),
            'offset': getattr(self, 'offset_box', type('obj', (object,), {'value': lambda: 0.0})).value(),
            'do_autoscale': getattr(self, 'autoscale_check', type('obj', (object,), {'isChecked': lambda: False})).isChecked(),
            'is_cont': getattr(self, 'continuous_check', type('obj', (object,), {'isChecked': lambda: False})).isChecked(),
            'load_imp': getattr(self, 'load_combo', type('obj', (object,), {'currentText': lambda: 'High-Z'})).currentText(),
            'duty_cycle': getattr(self, 'duty_box', type('obj', (object,), {'value': lambda: 50.0})).value(),
            'trig_edge': getattr(self, 'trig_edge_combo', type('obj', (object,), {'currentText': lambda: 'Rising'})).currentText(),
            'trig_level': getattr(self, 'trig_level_box', type('obj', (object,), {'value': lambda: 0.0})).value(),
            'auto_log': getattr(self, 'auto_log_check', type('obj', (object,), {'isChecked': lambda: False})).isChecked(),
            'log_interval': 5.0,
            'phase': getattr(self, 'phase_box', type('obj', (object,), {'value': lambda: 0.0})).value(),
            'timebase': getattr(self, 'timebase_box', type('obj', (object,), {'value': lambda: 0.001})).value(),
            'vdiv': getattr(self, 'vdiv_box', type('obj', (object,), {'value': lambda: 1.0})).value(),
            'coupling': getattr(self, 'copling_combo', type('obj', (object,), {'currentText': lambda: 'DC'})).currentText(), 
            'ch1_enable': getattr(self, 'ch1_enable_check', type('obj', (object,), {'isChecked': lambda: True})).isChecked(),
            'ch2_enable': getattr(self, 'ch2_enable_check', type('obj', (object,), {'isChecked': lambda: False})).isChecked(),
            'mod_type': getattr(self, 'mod_type_combo', type('obj', (object,), {'currentText': lambda: 'OFF'})).currentText(),
            'mod_freq': getattr(self, 'doubleSpinBox', type('obj', (object,), {'value': lambda: 100.0})).value(), 
            'mod_depth': getattr(self, 'doubleSpinBox_2', type('obj', (object,), {'value': lambda: 50.0})).value(), 
            'burst_enable': getattr(self, 'burst_check', type('obj', (object,), {'isChecked': lambda: False})).isChecked(),
            'burst_cycles': getattr(self, 'burst_cycle_box', type('obj', (object,), {'value': lambda: 1})).value(), 
            'polarity': getattr(self, 'polarity_combo', type('obj', (object,), {'currentText': lambda: 'Normal'})).currentText(),
            'v_offset': getattr(self, 'v_offset_box', type('obj', (object,), {'value': lambda: 0.0})).value(),
            'trig_mode': getattr(self, 'trig_mode_combo', type('obj', (object,), {'currentText': lambda: 'Auto'})).currentText(),
            'h_offset': getattr(self, 'h_offset_box', type('obj', (object,), {'value': lambda: 0.0})).value(),
            'math_mode': getattr(self, 'math_combo', type('obj', (object,), {'currentText': lambda: 'OFF'})).currentText(),
            'gen_output_on': getattr(self, 'gen_out_btn', type('obj', (object,), {'isChecked': lambda: True})).isChecked()
        }

        self.worker = HardwareWorker(self.scope, self.gen, self.current_params)
        self.worker.status_update.connect(self.update_status_label)
        self.worker.progress_update.connect(self.progress_bar.setValue) 
        self.worker.data_ready.connect(self.plot_new_data)
        self.worker.measurements_ready.connect(self.update_measurements) 
        self.worker.live_hardware_state.connect(self.update_live_dashboards)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.finished.connect(self.thread_finished)
        self.worker.start()

    def update_live_dashboards(self, state):
        def safe_float(val_str):
            try:
                clean_str = ''.join(c for c in str(val_str) if c.isdigit() or c in '.-+eE')
                return float(clean_str) if clean_str else None
            except: return None

        t_val = safe_float(state['s_time'])
        if t_val is not None:
            if t_val < 0.001: t_str = f"{t_val*1e6:.1f} µs/Div"
            elif t_val < 1.0: t_str = f"{t_val*1e3:.1f} ms/Div"
            else: t_str = f"{t_val:.2f} s/Div"
        else: t_str = state['s_time']

        v_val = safe_float(state['s_vdiv'])
        if v_val is not None:
            if v_val < 0.1: v_str = f"{v_val*1000:.1f} mV/Div"
            else: v_str = f"{v_val:.2f} V/Div"
        else: v_str = state['s_vdiv']

        trig_val = safe_float(state['s_trig'])
        trig_str = f"{trig_val:.2f} V" if trig_val is not None else state['s_trig']

        f_val = safe_float(state['g_freq'])
        if f_val is not None:
            if f_val >= 1e6: f_str = f"{f_val/1e6:.2f} MHz"
            elif f_val >= 1e3: f_str = f"{f_val/1e3:.2f} kHz"
            else: f_str = f"{f_val:.2f} Hz"
        else: f_str = state['g_freq']

        a_val = safe_float(state['g_amp'])
        amp_str = f"{a_val:.2f} Vpp" if a_val is not None else state['g_amp']

        if hasattr(self, 'live_scope_time'): self.live_scope_time.setText(f"Time/Div: {t_str}")
        if hasattr(self, 'live_scope_vdiv'): self.live_scope_vdiv.setText(f"Volts/Div: {v_str}")
        if hasattr(self, 'live_scope_trig'): self.live_scope_trig.setText(f"Trigger: {trig_str}")
        if hasattr(self, 'live_gen_wave'): self.live_gen_wave.setText(f"Waveform: {state['g_wave']}")
        if hasattr(self, 'live_gen_freq'): self.live_gen_freq.setText(f"Frequency: {f_str}")
        if hasattr(self, 'live_gen_amp'): self.live_gen_amp.setText(f"Amplitude: {amp_str}")

        color_css = "color: #00ffcc; font-weight: bold;"
        for lbl in ['live_scope_time', 'live_scope_vdiv', 'live_scope_trig', 'live_gen_wave', 'live_gen_freq', 'live_gen_amp']:
            if hasattr(self, lbl): getattr(self, lbl).setStyleSheet(color_css)

    def run_bode_sweep(self):
        if self.worker and self.worker.isRunning(): self.worker.stop()
        if not self.scope or not self.gen: return

        self.run_bode_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        
        start_f = getattr(self, 'bode_strart_box', type('obj', (object,), {'value': lambda: 10.0})).value()
        stop_f = getattr(self, 'bode_strart_box_2', type('obj', (object,), {'value': lambda: 10000.0})).value()
        steps = getattr(self, 'bode_steps_box', type('obj', (object,), {'value': lambda: 50})).value()
        amp = getattr(self, 'amp_box', type('obj', (object,), {'value': lambda: 2.0})).value()

        self.worker = BodeWorker(self.scope, self.gen, start_f, stop_f, steps, amp)
        self.worker.status_update.connect(self.update_status_label)
        self.worker.progress_update.connect(self.progress_bar.setValue)
        self.worker.bode_ready.connect(self.plot_bode_data)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.finished.connect(lambda: getattr(self, 'run_bode_btn', type('obj', (object,), {'setEnabled': lambda x: None})).setEnabled(True))
        self.worker.start()

    def update_measurements(self, vpp, freq):
        if hasattr(self, 'meas_vpp_label'): self.meas_vpp_label.setText(f"Scope Vpp: {vpp}")
        if hasattr(self, 'meas_freq_label'): self.meas_freq_label.setText(f"Scope Freq: {freq}")

    def clear_plot(self):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor('#1e1e1e') 
        self.figure.patch.set_facecolor('#323232') 
        ax.tick_params(colors='white')
        ax.set_title("Oscilloscope Ready", color='white')
        ax.grid(True, color='#555555', alpha=0.5)
        self.canvas.draw()

    def update_status_label(self, message):
        if hasattr(self, 'status_label'): self.status_label.setText(message)

    def plot_new_data(self, channel_data):
        self.current_channel_data = channel_data
        self.figure.clear()

        do_fft = hasattr(self, 'fft_check') and self.fft_check.isChecked()
        ax1 = self.figure.add_subplot(211) if do_fft else self.figure.add_subplot(111)
        
        ax1.set_facecolor('#1e1e1e') 
        ax1.set_title("Oscilloscope Capture", color='white')
        ax1.grid(True, color='#555555', alpha=0.5)
        ax1.tick_params(colors='white')

        t_ax = None
        v1 = None

        if "CHANnel1" in channel_data and self.current_params['ch1_enable']:
            t_ax, v1 = channel_data["CHANnel1"]
            ax1.plot(t_ax, v1, color='#00ffcc', linewidth=1.5, label='CH1') 

        if "CHANnel2" in channel_data and self.current_params['ch2_enable']:
            t_ax2, v2 = channel_data["CHANnel2"]
            ax1.plot(t_ax2, v2, color='#ffcc00', linewidth=1.5, label='CH2') 

        if self.current_params.get('math_mode', 'OFF') != "OFF" and "CHANnel1" in channel_data and "CHANnel2" in channel_data:
            v1_math, v2_math = channel_data["CHANnel1"][1], channel_data["CHANnel2"][1]
            math_v = None
            if self.current_params['math_mode'] == "CH1+CH2": math_v = v1_math + v2_math
            elif self.current_params['math_mode'] == "CH1-CH2": math_v = v1_math - v2_math
            elif self.current_params['math_mode'] == "CH1*CH2": math_v = v1_math * v2_math
            
            if math_v is not None:
                ax1.plot(channel_data["CHANnel1"][0], math_v, color='#ff00ff', linewidth=2.0, label=f'MATH ({self.current_params["math_mode"]})')

        ax1.legend(loc="upper right", facecolor='#323232', labelcolor='white')

        if do_fft and t_ax is not None and v1 is not None:
            ax2 = self.figure.add_subplot(212)
            ax2.set_facecolor('#1e1e1e') 
            ax2.set_title("Frequency Spectrum (FFT) - CH1", color='white')
            ax2.grid(True, color='#555555', alpha=0.5)
            ax2.tick_params(colors='white')
            
            n = len(v1)
            dt = t_ax[1] - t_ax[0]
            fft_freq = np.fft.rfftfreq(n, d=dt)
            fft_mag = np.abs(np.fft.rfft(v1)) / n 
            fft_mag[0] = 0 
            ax2.plot(fft_freq, fft_mag, color='#ff00ff', linewidth=1.5) 
            ax2.set_xlim(0, max(fft_freq)/4) 
            self.figure.subplots_adjust(hspace=0.4) 

        self.canvas.draw() 
        if hasattr(self, 'status_label'):
            self.status_label.setText("Status: Live...")
            self.status_label.setStyleSheet("color: #00FF00;")

    def plot_bode_data(self, freqs, vpps):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor('#1e1e1e') 
        ax.set_title("Bode Magnitude Plot", color='white')
        ax.set_xlabel("Frequency (Hz)", color='white')
        ax.set_ylabel("Amplitude (Vpp)", color='white')
        
        ax.set_xscale('log')
        ax.plot(freqs, vpps, color='#ffaa00', marker='o', linestyle='-', linewidth=2) 
        
        ax.grid(True, which="both", color='#555555', alpha=0.5)
        ax.tick_params(colors='white')
        self.canvas.draw() 

    def handle_error(self, error_msg):
        if hasattr(self, 'status_label'):
            self.status_label.setText("Status: Error!")
            self.status_label.setStyleSheet("color: red;")
        if hasattr(self, 'progress_bar'):
            self.progress_bar.setValue(0)
        print(f"Thread Error: {error_msg}")

    def thread_finished(self):
        if hasattr(self, 'run_btn'):
            self.run_btn.setEnabled(True) 
            self.run_btn.setText("Apply & Capture")
            self.run_btn.setStyleSheet("") 

    def save_image(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Plot", "waveform.png", "PNG (*.png)", options=options)
        if file_path: self.figure.savefig(file_path, facecolor=self.figure.get_facecolor()) 

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop(); self.worker.wait() 
        if self.gen:
            try: self.gen.write(":OUTPut1:STATe OFF"); self.gen.close()
            except: pass
        if self.scope:
            try: self.scope.close()
            except: pass
        if self.rm: self.rm.close()
        event.accept()

# Dark Theme Stylesheet
dark_stylesheet = """
QMainWindow, QWidget { background-color: #323232; color: white; }
QGroupBox { border: 1px solid #505050; border-radius: 5px; margin-top: 1ex; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
QTabWidget::pane { border: 1px solid #505050; }
QTabBar::tab { background: #404040; padding: 8px; border: 1px solid #505050; }
QTabBar::tab:selected { background: #1e1e1e; font-weight: bold; }
QPushButton { background-color: #505050; border: 1px solid #707070; padding: 5px; border-radius: 3px; }
QPushButton:hover { background-color: #606060; }
QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox { background-color: #1e1e1e; border: 1px solid #505050; padding: 3px; }
QProgressBar { border: 1px solid #505050; text-align: center; background-color: #1e1e1e; }
QProgressBar::chunk { background-color: #007acc; }
QLabel { font-weight: bold; }
QTextEdit, QPlainTextEdit { background-color: #1e1e1e; color: #00ffcc; border: 1px solid #505050; font-family: monospace; padding: 5px; }
"""

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(dark_stylesheet)
    window = LabGUI()
    window.show()
    sys.exit(app.exec_())