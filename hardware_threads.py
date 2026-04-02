# -*- coding: utf-8 -*-
"""
Created on Thu Apr  2 19:42:36 2026

@author: pkhan
"""

import time
import os
from datetime import datetime
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

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
    live_hardware_state = pyqtSignal(dict) 

    def __init__(self, scope, gen, params):
        super().__init__()
        self.scope = scope
        self.gen = gen
        self.p = params 
        self.running = True 

    def run(self):
        try:
            # 1. Setup Generator
            self.status_update.emit("Setting Generator...")
            self.progress_update.emit(10)
            
            imp = "INFinity" if self.p['load_imp'] == "High-Z" else "50"
            self.gen.write(f":OUTPut1:IMPedance {imp}")
            self.gen.write(f":SOURce1:APPLy:{self.p['shape']} {self.p['freq']}, {self.p['amp']}, {self.p['offset']}")

            if self.p['shape'] == "SQUare": self.gen.write(f":SOURce1:FUNCtion:SQUare:DCYCle {self.p['duty_cycle']}")
            elif self.p['shape'] == "RAMP": self.gen.write(f":SOURce1:FUNCtion:RAMP:SYMMetry {self.p['duty_cycle']}")

            self.gen.write(f":SOURce1:PHASe {self.p['phase']}")
            self.gen.write(f":OUTPut1:POLarity {'INVerted' if self.p['polarity'] == 'Inverted' else 'NORMal'}")

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

            if self.p.get('gen_output_on', True):
                self.gen.write(":OUTPut1:STATe ON")
            else:
                self.gen.write(":OUTPut1:STATe OFF")
            time.sleep(0.5) 

            # 2. Setup Scope
            if self.p['ch1_enable']: self.scope.write(":CHANnel1:DISPlay ON")
            else: self.scope.write(":CHANnel1:DISPlay OFF")
                
            if self.p['ch2_enable']: self.scope.write(":CHANnel2:DISPlay ON")
            else: self.scope.write(":CHANnel2:DISPlay OFF")

            self.scope.write(f":CHANnel1:COUPling {self.p['coupling']}")

            if self.p['do_autoscale']:
                self.status_update.emit("Autoscaling Scope...")
                self.scope.write(":AUToscale")
                for i in range(20, 70, 2): 
                    if not self.running: return 
                    time.sleep(0.1) 
                    self.progress_update.emit(i)
            else:
                self.status_update.emit("Applying Manual Scope Scale...")
                self.scope.write(f":TIMebase:MAIN:SCALe {self.p['timebase']}")
                self.scope.write(f":TIMebase:MAIN:OFFSet {self.p['h_offset']}")
                self.scope.write(f":CHANnel1:SCALe {self.p['vdiv']}")
                self.scope.write(f":CHANnel1:OFFSet {self.p['v_offset']}")
                if self.p['ch2_enable']:
                    self.scope.write(f":CHANnel2:SCALe {self.p['vdiv']}")
                    self.scope.write(f":CHANnel2:OFFSet {self.p['v_offset']}")
                self.progress_update.emit(50)
            
            self.status_update.emit("Setting Trigger...")
            self.scope.write(f":TRIGger:SWEep {self.p['trig_mode'][:4].upper()}")
            self.scope.write(":TRIGger:EDGe:SOURce CHANnel1")
            self.scope.write(f":TRIGger:EDGe:SLOPe {'POSitive' if self.p['trig_edge'] == 'Rising' else 'NEGative'}")
            self.scope.write(f":TRIGger:EDGe:LEVel {self.p['trig_level']}")
            self.progress_update.emit(80) 

            # Read Dashboard State
            self.status_update.emit("Reading Active State...")
            try:
                gen_state = self.gen.query(":SOURce1:APPLy?").strip().replace('"', '').split(',')
                self.live_hardware_state.emit({
                    's_time': self.scope.query(":TIMebase:MAIN:SCALe?").strip(),
                    's_vdiv': self.scope.query(":CHANnel1:SCALe?").strip(),
                    's_trig': self.scope.query(":TRIGger:EDGe:LEVel?").strip(),
                    'g_wave': gen_state[0] if len(gen_state) > 0 else "--",
                    'g_freq': gen_state[1] if len(gen_state) > 1 else "--",
                    'g_amp': gen_state[2] if len(gen_state) > 2 else "--"
                })
            except Exception as e:
                print("Failed to read active state:", e)

            # 3. Live Loop
            self.status_update.emit("Live Monitoring..." if self.p['is_cont'] else "Fetching Data...")
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
                    params = [float(x) for x in self.scope.query(":WAVeform:PREamble?").split(',')]
                    xinc, xorig, yinc, yorig, yref = params[4], params[5], params[7], params[8], params[9]

                    raw_data = self.scope.query_binary_values(":WAVeform:DATA?", datatype='B', container=np.array, header_fmt='ieee')
                    volts = (raw_data - yorig - yref) * yinc
                    time_axis = np.arange(len(volts)) * xinc + xorig
                    channel_data[chan] = (time_axis, volts)

                self.progress_update.emit(100)
                self.data_ready.emit(channel_data)

                if self.p['auto_log'] and self.p['is_cont'] and (time.time() - last_log_time) >= self.p['log_interval']:
                    filename = f"AutoLogs/log_{self.p['shape']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    if "CHANnel1" in channel_data:
                        np.savetxt(filename, np.column_stack(channel_data["CHANnel1"]), delimiter=",", header="Time(s),Volts(V)", comments="")
                    last_log_time = time.time()

                if not self.p['is_cont']: break 
                time.sleep(0.1) 

        except Exception as e: self.error_occurred.emit(str(e))
        finally: self.finished.emit()

    def stop(self): self.running = False


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
        self.scope, self.gen, self.start_f, self.stop_f, self.steps, self.amp = scope, gen, start_f, stop_f, steps, amp
        self.running = True 

    def run(self):
        try:
            self.gen.write(f":SOURce1:MOD:STATe OFF\n:SOURce1:APPLy:SINusoid {self.start_f}, {self.amp}, 0\n:OUTPut1:STATe ON")
            freqs = np.logspace(np.log10(self.start_f), np.log10(self.stop_f), self.steps)
            vpps = []

            for i, f in enumerate(freqs):
                if not self.running: break
                self.status_update.emit(f"Sweeping... {f:.1f} Hz")
                self.progress_update.emit(int((i / self.steps) * 100))
                
                self.gen.write(f":SOURce1:FREQuency {f}")
                self.scope.write(f":TIMebase:MAIN:SCALe {max((1.0 / f) * 2.0, 5e-9)}")
                time.sleep(0.4) 
                
                try: vpps.append(float(self.scope.query(":MEASure:VPP? CHANnel1")))
                except: vpps.append(0.0) 

            self.bode_ready.emit(np.array(freqs[:len(vpps)]), np.array(vpps))
            self.progress_update.emit(100)
            self.status_update.emit("Bode Sweep Complete!")
        except Exception as e: self.error_occurred.emit(str(e))
        finally: self.finished.emit()

    def stop(self): self.running = False