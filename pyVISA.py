import sys
import time
import os
from datetime import datetime
import numpy as np
import pyvisa
from PyQt5 import QtWidgets, uic
from PyQt5.QtWidgets import QApplication, QFileDialog
from PyQt5.QtCore import QThread, pyqtSignal
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar 
from matplotlib.figure import Figure

# --- YOUR INSTRUMENT ADDRESSES ---
SCOPE_VISA_ADDRESS = "USB0::0x1AB1::0x04B0::DS2D245104188::INSTR" 
GEN_VISA_ADDRESS = "USB0::0x1AB1::0x0641::DG4C145200907::INSTR" 

# ==========================================
# THREAD 1: Standard Live Mode & FFT
# ==========================================
class HardwareWorker(QThread):
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    data_ready = pyqtSignal(dict) 
    measurements_ready = pyqtSignal(str, str) 
    error_occurred = pyqtSignal(str)
    finished = pyqtSignal()
    
    # Signal for Live Dashboard
    live_hardware_state = pyqtSignal(dict) 

    def __init__(self, scope, gen, params):
        super().__init__()
        self.scope = scope
        self.gen = gen
        self.p = params 
        self.running = True 

    def run(self):
        try:
            # --- 1. SETUP WAVEFORM GENERATOR ---
            self.progress_update.emit(10)
            self.status_update.emit("Status: Setting Generator...")
            
            imp = "INFinity" if self.p['load_imp'] == "High-Z" else "50"
            self.gen.write(f":OUTPut1:IMPedance {imp}")
            
            cmd = f":SOURce1:APPLy:{self.p['shape']} {self.p['freq']}, {self.p['amp']}, {self.p['offset']}"
            self.gen.write(cmd)

            if self.p['shape'] == "SQUare": self.gen.write(f":SOURce1:FUNCtion:SQUare:DCYCle {self.p['duty_cycle']}")
            elif self.p['shape'] == "RAMP": self.gen.write(f":SOURce1:FUNCtion:RAMP:SYMMetry {self.p['duty_cycle']}")

            self.gen.write(f":SOURce1:PHASe {self.p['phase']}")
            pol = "INVerted" if self.p['polarity'] == "Inverted" else "NORMal"
            self.gen.write(f":OUTPut1:POLarity {pol}")

            if self.p['burst_enable']:
                self.gen.write(":SOURce1:BURSt:MODE TRIGgered")
                self.gen.write(f":SOURce1:BURSt:NCYCles {self.p['burst_cycles']}")
                self.gen.write(":SOURce1:BURSt:STATe ON")
            else:
                self.gen.write(":SOURce1:BURSt:STATe OFF")

            if self.p['mod_type'] == "OFF":
                self.gen.write(":SOURce1:MOD:STATe OFF")
            else:
                self.gen.write(f":SOURce1:MOD:TYPe {self.p['mod_type']}")
                if self.p['mod_type'] == "AM":
                    self.gen.write(f":SOURce1:MOD:AM:INTernal:FREQuency {self.p['mod_freq']}")
                    self.gen.write(f":SOURce1:MOD:AM:DEPTh {self.p['mod_depth']}")
                elif self.p['mod_type'] == "FM":
                    self.gen.write(f":SOURce1:MOD:FM:INTernal:FREQuency {self.p['mod_freq']}")
                    self.gen.write(f":SOURce1:MOD:FM:DEViation {self.p['mod_depth']}")
                self.gen.write(":SOURce1:MOD:STATe ON")

            # Respect GUI Toggle Button
            if self.p.get('gen_output_on', True):
                self.gen.write(":OUTPut1:STATe ON")
            else:
                self.gen.write(":OUTPut1:STATe OFF")
            time.sleep(0.5) 

            # --- 2. SETUP OSCILLOSCOPE ---
            
            # ---> FIX 2: FORCE PHYSICAL SCOPE CHANNELS ON/OFF <---
            if self.p['ch1_enable']:
                self.scope.write(":CHANnel1:DISPlay ON")
            else:
                self.scope.write(":CHANnel1:DISPlay OFF")
                
            if self.p['ch2_enable']:
                self.scope.write(":CHANnel2:DISPlay ON")
            else:
                self.scope.write(":CHANnel2:DISPlay OFF")

            self.scope.write(f":CHANnel1:COUPling {self.p['coupling']}")

            if self.p['do_autoscale']:
                self.status_update.emit("Status: Autoscaling Scope...")
                self.scope.write(":AUToscale")
                for i in range(20, 70, 2): 
                    if not self.running: return 
                    time.sleep(0.1) 
                    self.progress_update.emit(i)
            else:
                self.status_update.emit("Status: Applying Manual Scope Scale...")
                self.scope.write(f":TIMebase:MAIN:SCALe {self.p['timebase']}")
                self.scope.write(f":TIMebase:MAIN:OFFSet {self.p['h_offset']}")
                
                self.scope.write(f":CHANnel1:SCALe {self.p['vdiv']}")
                self.scope.write(f":CHANnel1:OFFSet {self.p['v_offset']}")
                if self.p['ch2_enable']:
                    self.scope.write(f":CHANnel2:SCALe {self.p['vdiv']}")
                    self.scope.write(f":CHANnel2:OFFSet {self.p['v_offset']}")
                self.progress_update.emit(50)
            
            self.status_update.emit("Status: Setting Trigger...")
            trig_mode = self.p['trig_mode'][:4].upper() 
            self.scope.write(f":TRIGger:SWEep {trig_mode}")
            self.scope.write(":TRIGger:EDGe:SOURce CHANnel1")
            edge = "POSitive" if self.p['trig_edge'] == "Rising" else "NEGative"
            self.scope.write(f":TRIGger:EDGe:SLOPe {edge}")
            self.scope.write(f":TRIGger:EDGe:LEVel {self.p['trig_level']}")

            self.progress_update.emit(80) 

            # --- READ ACTIVE STATE FOR DASHBOARD ---
            self.status_update.emit("Status: Reading Active State...")
            try:
                act_time = self.scope.query(":TIMebase:MAIN:SCALe?").strip()
                act_vdiv = self.scope.query(":CHANnel1:SCALe?").strip()
                act_trig = self.scope.query(":TRIGger:EDGe:LEVel?").strip()
                
                gen_state = self.gen.query(":SOURce1:APPLy?").strip().replace('"', '').split(',')
                
                active_state = {
                    's_time': act_time,
                    's_vdiv': act_vdiv,
                    's_trig': act_trig,
                    'g_wave': gen_state[0] if len(gen_state) > 0 else "--",
                    'g_freq': gen_state[1] if len(gen_state) > 1 else "--",
                    'g_amp': gen_state[2] if len(gen_state) > 2 else "--"
                }
                self.live_hardware_state.emit(active_state)
            except Exception as e:
                print("Failed to read active state:", e)

            # --- 3. LIVE FETCH LOOP ---
            self.status_update.emit("Status: Live Monitoring..." if self.p['is_cont'] else "Status: Fetching Data...")
            self.scope.write(":WAVeform:MODE NORMal")
            self.scope.write(":WAVeform:FORMat BYTE")

            channels_to_fetch = []
            if self.p['ch1_enable'] or self.p['math_mode'] != "OFF": channels_to_fetch.append("CHANnel1")
            if self.p['ch2_enable'] or self.p['math_mode'] != "OFF": 
                if "CHANnel2" not in channels_to_fetch: channels_to_fetch.append("CHANnel2")

            last_log_time = time.time()
            if self.p['auto_log']: os.makedirs("AutoLogs", exist_ok=True)

            while self.running:
                try:
                    raw_vpp = float(self.scope.query(":MEASure:VPP? CHANnel1"))
                    raw_freq = float(self.scope.query(":MEASure:FREQuency? CHANnel1"))
                    self.measurements_ready.emit(f"{raw_vpp:.2f} V", f"{raw_freq:.2f} Hz" if raw_freq < 1e15 else "N/A")
                except: pass 

                channel_data = {}
                for chan in channels_to_fetch:
                    self.scope.write(f":WAVeform:SOURce {chan}")
                    preamble = self.scope.query(":WAVeform:PREamble?")
                    params = preamble.split(',')
                    xinc, xorig, xref = float(params[4]), float(params[5]), float(params[6])
                    yinc, yorig, yref = float(params[7]), float(params[8]), float(params[9])

                    raw_data = self.scope.query_binary_values(":WAVeform:DATA?", datatype='B', container=np.array, header_fmt='ieee')
                    volts = (raw_data - yorig - yref) * yinc
                    time_axis = np.arange(len(volts)) * xinc + xorig
                    channel_data[chan] = (time_axis, volts)

                self.progress_update.emit(100)
                self.data_ready.emit(channel_data)

                if self.p['auto_log'] and self.p['is_cont']:
                    current_time = time.time()
                    if (current_time - last_log_time) >= self.p['log_interval']:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"AutoLogs/log_{self.p['shape']}_{timestamp}.csv"
                        if "CHANnel1" in channel_data:
                            t_ax, v_ax = channel_data["CHANnel1"]
                            np.savetxt(filename, np.column_stack((t_ax, v_ax)), delimiter=",", header="Time(s),Volts(V)", comments="")
                        last_log_time = current_time 

                if not self.p['is_cont']: break 
                time.sleep(0.1) 

        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            self.finished.emit()

    def stop(self):
        self.running = False


# ==========================================
# THREAD 2: Automated Bode Plot Sweep
# ==========================================
class BodeWorker(QThread):
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    bode_ready = pyqtSignal(np.ndarray, np.ndarray) 
    error_occurred = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, scope, gen, start_f, stop_f, steps, amp):
        super().__init__()
        self.scope = scope
        self.gen = gen
        self.start_f = start_f
        self.stop_f = stop_f
        self.steps = steps
        self.amp = amp
        self.running = True 

    def run(self):
        try:
            self.gen.write(":SOURce1:MOD:STATe OFF") 
            self.gen.write(f":SOURce1:APPLy:SINusoid {self.start_f}, {self.amp}, 0")
            self.gen.write(":OUTPut1:STATe ON")
            
            freqs = np.logspace(np.log10(self.start_f), np.log10(self.stop_f), self.steps)
            vpps = []

            for i, f in enumerate(freqs):
                if not self.running: break
                
                self.status_update.emit(f"Status: Sweeping... {f:.1f} Hz")
                self.progress_update.emit(int((i / self.steps) * 100))
                
                self.gen.write(f":SOURce1:FREQuency {f}")
                timebase = max((1.0 / f) * 2.0, 5e-9)
                self.scope.write(f":TIMebase:MAIN:SCALe {timebase}")
                time.sleep(0.4) 
                
                try:
                    vpp = float(self.scope.query(":MEASure:VPP? CHANnel1"))
                    vpps.append(vpp)
                except:
                    vpps.append(0.0) 

            self.bode_ready.emit(np.array(freqs[:len(vpps)]), np.array(vpps))
            self.progress_update.emit(100)
            self.status_update.emit("Status: Bode Sweep Complete!")

        except Exception as e: self.error_occurred.emit(str(e))
        finally: self.finished.emit()

    def stop(self): self.running = False


# ==========================================
# MAIN GUI
# ==========================================
class LabGUI(QtWidgets.QMainWindow):
    def __init__(self):
        super(LabGUI, self).__init__()
        uic.loadUi('instrument_gui.ui', self)
        
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
        
        # Connect Main Buttons
        self.run_btn.clicked.connect(self.toggle_capture)
        if hasattr(self, 'run_bode_btn'):
            self.run_bode_btn.clicked.connect(self.run_bode_sweep) 
        self.save_img_btn.clicked.connect(self.save_image)
        self.clear_btn.clicked.connect(self.clear_plot)
        if hasattr(self, 'read_param_btn'):
            self.read_param_btn.clicked.connect(self.read_hardware_params)
            
        # Connect New Gen Toggle Button
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
        if not self.scope or not self.gen: return
        if self.worker and self.worker.isRunning(): return
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

    # ---> FIX 1: BULLETPROOF NUMBER CLEANER <---
    def update_live_dashboards(self, state):
        """Updates the UI panels with actual hardware data formatted nicely"""
        
        # A smart helper function to strip out invisible garbage characters
        def safe_float(val_str):
            try:
                clean_str = ''.join(c for c in str(val_str) if c.isdigit() or c in '.-+eE')
                return float(clean_str) if clean_str else None
            except: return None

        # 1. Format Time/Div
        t_val = safe_float(state['s_time'])
        if t_val is not None:
            if t_val < 0.001: t_str = f"{t_val*1e6:.1f} µs/Div"
            elif t_val < 1.0: t_str = f"{t_val*1e3:.1f} ms/Div"
            else: t_str = f"{t_val:.2f} s/Div"
        else: t_str = state['s_time']

        # 2. Format Volts/Div
        v_val = safe_float(state['s_vdiv'])
        if v_val is not None:
            if v_val < 0.1: v_str = f"{v_val*1000:.1f} mV/Div"
            else: v_str = f"{v_val:.2f} V/Div"
        else: v_str = state['s_vdiv']

        # 3. Format Trigger Level
        trig_val = safe_float(state['s_trig'])
        if trig_val is not None:
            trig_str = f"{trig_val:.2f} V"
        else: trig_str = state['s_trig']

        # 4. Format Generator Frequency
        f_val = safe_float(state['g_freq'])
        if f_val is not None:
            if f_val >= 1e6: f_str = f"{f_val/1e6:.2f} MHz"
            elif f_val >= 1e3: f_str = f"{f_val/1e3:.2f} kHz"
            else: f_str = f"{f_val:.2f} Hz"
        else: f_str = state['g_freq']

        # 5. Format Generator Amplitude
        a_val = safe_float(state['g_amp'])
        if a_val is not None:
            amp_str = f"{a_val:.2f} Vpp"
        else: amp_str = state['g_amp']

        # Update Labels
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