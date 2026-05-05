from time import ctime, time, sleep
import numpy as np
import pyvisa
import os
import csv
import io

class ConditionalFileWriter:
    def __init__(self, filename, should_write):
        name,extension = os.path.splitext(filename)
        i=1
        while os.path.isfile(filename):
            filename = name + "_" + str(i) + extension
            i+=1
        self.filename = filename
        self.should_write = should_write

    def __enter__(self):
        if self.should_write:
            self.file = open(self.filename, 'w', newline='')
        else:
            self.file = io.StringIO()
        return self.file

    def __exit__(self, exc_type, exc_value, traceback):
        self.file.close()

class InstrumentGroup():
    """A class for controlling a group of instruments and recording data."""
    def __init__(self, **kwargs):
        """Initializes the InstrumentGroup class.
        
        Keyword Arguments
        ----------
        voltmeters : list of tuples
            A list of tuples containing the name and voltmeter object for each
            voltmeter. The name is a string, and the voltmeter object is an
            instance of the Voltmeter class.
        sourcemeters : list of tuples
            A list of tuples containing the name and sourcemeter object for each
            sourcemeter. The name is a string, and the sourcemeter object is an
            instance of the Sourcemeter class.
        Vsourcemeters : list of tuples
            A list of tuples containing the name and Vsourcemeter object for 
            each Vsourcemeter. The name is a string, and the Vsourcemeter object
            is an instance of the Vsourcemeter class.
        iTC : MercuryiTC object
            An instance of the MercuryiTC class, the temperature controller for 
            the Teslatron system.
        iPS : MercuryiPS object
            An instance of the MercuryiPS class, the magnet power supply for the
            Teslatron system.
        lakeshore: Lakeshore object
            An instance of the Lakeshore class, for additional temperature 
            measurement.
        
        Returns
        -------
        None
        """
        self.voltmeters = kwargs.get("voltmeters", [])
        self.sourcemeters = kwargs.get("sourcemeters", [])
        self.Vsourcemeters = kwargs.get("Vsourcemeters", [])
        self.iTC = kwargs.get("iTC", None)
        self.iPS = kwargs.get("iPS", None)
        self.lakeshore = kwargs.get("lakeshore", None)
        self.comment = kwargs.get("comment", " ")
        self.filename = kwargs.get("filename", "You_forgot_to_set_a_filename.txt")
        self.measure = kwargs.get("measure", True)

    def set_filename(self,filename):
        self.filename = filename
        self.measure = True
    
    def set_comment(self,comment):
        self.comment = comment
    
    def dont_measure(self):
        self.measure = False

    def get_headers(self):
        """Returns a list of headers for the data file"""
        headers = ["Time"]
        if self.iTC:
            headers += ["T_probe (K)", "T_probe_setpoint (K)", "T_probe_ramp_rate (K/min)", 
                        "heater_probe (%)", "T_VTI (K)", "T_VTI_setpoint (K)", "T_VTI_ramp_rate (K/min)", 
                        "heater_VTI (%)","Pressure (mB)","Needlevalve"]
        if self.iPS:
            headers += ["B (T)", "B_setpoint (T)", "B_ramp_rate (T/min)"]
        for name,sourcemeter in self.sourcemeters:
            headers.append(f"I_{name} (A)")
        for name,Vsourcemeter in self.Vsourcemeters:
            headers.append(f"Vg_{name} (V)")
            headers.append(f"Ileak_{name} (A)")
        for name,voltmeter in self.voltmeters:
            headers.append(f"V+_{name} (V)")
        for name,voltmeter in self.voltmeters:
            headers.append(f"V-_{name} (V)")
        for Iname,sourcemeter in self.sourcemeters:
            for Vname,voltmeter in self.voltmeters:
                headers.append(f"R_{Iname}{Vname} (ohm)")
        if self.lakeshore:
            headers += ["T_sample (K)"]
            headers += ["T_sample_err (K)"]
        return headers
    
    @staticmethod
    def round_to_significant_figures(num, sig_figs):
        if num != 0:
            return round(num, -int(np.floor(np.log10(abs(num))) + (1 - sig_figs)))
        else:
            return 0  # Can't take the log of 0
    
    def read_everything(self,time0=0):
        """Collects data from all instruments and returns a list"""
        Is = []
        Vps = []
        Vns = []
        lakeshoreT=[]
        data = [round(time()-time0,2)]
        for name,voltmeter in self.voltmeters:
            voltmeter.start_voltage_measurement()
        if self.iTC:
            data += [self.iTC.get_probe_temp(),
                     self.iTC.get_probe_setpoint(),
                     self.iTC.get_probe_ramp_rate(),
                     self.iTC.get_probe_heater(),
                     self.iTC.get_VTI_temp(),
                     self.iTC.get_VTI_setpoint(),
                     self.iTC.get_VTI_ramp_rate(),
                     self.iTC.get_VTI_heater(),
                     self.iTC.get_pressure(),
                     self.iTC.get_needlevalve()]
        if self.lakeshore:
            lakeshoreT += [self.lakeshore.get_temp(),
                           self.lakeshore.get_temp(),
                           self.lakeshore.get_temp()]
        if self.iPS:
            data += [self.iPS.get_field(),
                     self.iPS.get_field_setpoint(),
                     self.iPS.get_field_sweep_rate()]
        for name,sourcemeter in self.sourcemeters:
            Is += [sourcemeter.get_current()]
        data += Is
        for name,Vsourcemeter in self.Vsourcemeters:
            Vg,Ileak = Vsourcemeter.get_voltage_and_Ileak()
            data += [Vg,Ileak]
        for name,voltmeter in self.voltmeters:
            Vps += [voltmeter.get_voltage_measurement()]
        data += Vps
        for name,sourcemeter in self.sourcemeters:
            sourcemeter.reverse_current()
        if self.lakeshore:
            lakeshoreT += [self.lakeshore.get_temp(),
                           self.lakeshore.get_temp(),
                           self.lakeshore.get_temp()]
        for name,voltmeter in self.voltmeters:
            voltmeter.start_voltage_measurement()
        for name,voltmeter in self.voltmeters:
            Vns += [voltmeter.get_voltage_measurement()]
        data += Vns
        for I in Is:
            for Vp,Vn in zip(Vps,Vns):
                try:
                    data += [self.round_to_significant_figures(0.5*(Vp-Vn)/I,9)]
                except:
                    data += [np.nan]
        for name,sourcemeter in self.sourcemeters:
            sourcemeter.reverse_current()
        if self.lakeshore:
            lakeshoreT += [self.lakeshore.get_temp(),
                           self.lakeshore.get_temp(),
                           self.lakeshore.get_temp()]
            data += [round(np.mean(lakeshoreT),4)]
            data += [round(np.ptp(lakeshoreT),4)]
        return data
    
    def flush_and_reset(self):
        """ Flush buffers for mercury controllers, reset current to positive value"""
        for name,sourcemeter in self.sourcemeters:
            sourcemeter.set_current(abs(sourcemeter.get_current(nanforcompliance=False)))
        if self.iTC:
            print("Flushing iTC buffer")
            flushed=False
            while not flushed:
                try:
                    self.iTC.timeout = 500
                    self.iTC.query("")
                except pyvisa.VisaIOError as err:
                    if err.abbreviation=='VI_ERROR_TMO':
                        flushed=True
                        pass
                    else:
                        raise
        if self.iPS:
            print("Flushing iPS buffer")
            flushed=False
            while not flushed:
                try:
                    self.iPS.timeout = 500
                    self.iPS.query("")
                except pyvisa.VisaIOError as err:
                    if err.abbreviation=='VI_ERROR_TMO':
                        flushed=True
                        pass
                    else:
                        raise

    def print_current_vals(self):
        """Make one measurement from every instrument and print the values"""
        headers=self.get_headers()
        data=self.read_everything()
        for header,datum in zip(headers,data):
            print(f"{header}: {datum}")
        if self.iPS:
            print(f"PT1: {self.iPS.get_PT1_T()} K")
            print(f"PT2: {self.iPS.get_PT2_T()} K")
            print(f"Magnet T: {self.iPS.get_magnet_T()} K")
        return
    
    @staticmethod
    def make_list(x):
        """Converts a single value to a list, returns x as a list if it is 
        already a list or numpy array."""
        if hasattr(x,'__iter__'):
            return list(x)
        else:
            return [x]
        
    def measure_until_interrupted(self,timeout_hours=18):
        """Measures data until the user interrupts the measurement with Ctrl+C, 
        stop, or the timeout is reached.
        
        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end.
        timeout_hours : float, optional
            The number of hours to measure for before stopping. Default is 12.
        comment : str, optional
            A comment to write to the file header.

        Returns
        -------
        None
            Data is written to a file.
        """
        try:
            time0 = time()
            timeout=timeout_hours*3600
            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                print("Measuring continuously")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],["Continuous measurement"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])
                measuring=True
                while measuring:
                    data = self.read_everything(time0=time0)
                    writer.writerows([data])
                    f.flush()
                    sleep(0.01)

                    if time()-time0 > timeout:
                        measuring=False
                        print("Timeout reached")
                        break
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise
    
    def ramp_T(self,controller,Ts,rates,threshold=0.05,base_T_threshold=0.001,timeout_hours=18):
        """Ramps the temperature and records data continuously to a file.
        
        The ramp is considered complete when the temperature is within the 
        threshold of the setpoint (temperature is within setpoint +- threshold 
        for for 20 consecutive measurements), or when the temperature has not 
        changed by more than the base_T_threshold for 20 consecutive 
        measurements (the temperature is not changing, can occur if the set 
        point is below the base temperature).

        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end.
        controller : str
            The temperature controller to ramp.
            Must be either 'probe', 'VTI', or 'both'.
        Ts : float or list of floats or numpy array of floats
            The setpoint temperature(s) in K.
        rates : float or list of floats
            The ramp rate(s) in K/min.
        threshold : float, optional
            The threshold within which temperature to be considered at the 
            setpoint. The default is 0.05 K.
        base_T_threshold : float, optional
            The threshold for consecutive temperatures to be considered not 
            changing. The default is 0.005 K.
        timeout_hours : float, optional
            The number of hours to measure for before stopping. Default is 12.
            The timeout is reset for each setpoint.
        comment : str, optional
            A comment to write to the file header.
        
        Returns
        -------
        None
            Data is written to a file.
        """
        try:
            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],[f"Ramp {controller} T"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])

                Ts = self.make_list(Ts)
                rates = self.make_list(rates)
                if len(rates)==1:
                    rates=rates*len(Ts)
                if len(Ts) != len(rates):
                    print("Warning: length of T and rate lists are not equal")

                for T,rate in zip(Ts,rates):
                    if (rate==0) or (rate==np.nan) or (rate==np.inf):
                        match controller:
                            case "probe":
                                self.iTC.set_probe_temp(T)
                            case "VTI":
                                self.iTC.set_VTI_temp(T)
                            case "both":
                                self.iTC.set_probe_temp(T)
                                self.iTC.set_VTI_temp(T)
                            case _:
                                raise ValueError("Invalid controller, use 'probe', 'VTI', or 'both'")
                        print(f"Setting {controller} to {T} K")
                        min_time=60
                    else:
                        match controller:
                            case "probe":
                                min_time = 60*abs(T-self.iTC.get_probe_temp())/rate
                                self.iTC.ramp_probe_temp(T,rate)
                            case "VTI":
                                min_time = 60*abs(T-self.iTC.get_VTI_temp())/rate
                                self.iTC.ramp_VTI_temp(T,rate)
                            case "both":
                                min_time = max(60*abs(T-self.iTC.get_VTI_temp())/rate,60*abs(T-self.iTC.get_probe_temp())/rate)
                                self.iTC.ramp_probe_temp(T,rate)
                                self.iTC.ramp_VTI_temp(T,rate)
                            case _:
                                raise ValueError("Invalid controller. Use 'probe', 'VTI', or 'both'")
                        print(f"Ramping {controller} to {T} K at {rate} K/min")
                        

                    time0 = time()
                    timeout=timeout_hours*3600

                    T_reached = 0
                    T_stopped = 0
                    T_diff_sign_change = 0

                    prev_VTI_T=0
                    prev_VTI_diff=0
                    prev_probe_T=0
                    prev_probe_diff=0

                    time0=time()
                    measuring = True
                    while measuring:
                        data = self.read_everything(time0=time0)
                        writer.writerows([data])
                        f.flush()
                        sleep(0.01)

                        probe_T = self.iTC.get_probe_temp()
                        VTI_T = self.iTC.get_VTI_temp()
                        probe_diff = probe_T - prev_probe_T
                        VTI_diff = VTI_T - prev_VTI_T
                        match controller:
                            case "probe":
                                if abs(probe_T-T) < threshold:
                                    T_reached += 1
                                else:
                                    T_reached -= 10
                                if abs(probe_diff) < base_T_threshold:
                                    T_stopped += 1
                                else:
                                    T_stopped -= 10
                                    if T_reached<0: # both conditions not met
                                        T_diff_sign_change -= 5
                                if probe_diff*prev_probe_diff<0:
                                    T_diff_sign_change += 1
                            case "VTI":
                                if abs(VTI_T-T) < threshold:
                                    T_reached += 1
                                else:
                                    T_reached = -10
                                if abs(VTI_diff) < base_T_threshold:
                                    T_stopped += 1
                                else:
                                    T_stopped -= 10
                                    if T_reached<0: # both conditions not met
                                        T_diff_sign_change -= 5
                                if VTI_diff*prev_VTI_diff<0:
                                    T_diff_sign_change += 1
                            case "both":
                                if (abs(probe_T-T) < threshold) and (abs(VTI_T-T) < threshold):
                                    T_reached += 1
                                else:
                                    T_reached = -10
                                if (abs(probe_diff) < base_T_threshold) and (abs(VTI_diff) < base_T_threshold):
                                    T_stopped += 1
                                else:
                                    T_stopped -= 10
                                    if T_reached<0: # both conditions not met
                                        T_diff_sign_change -= 5
                                if (probe_diff*prev_probe_diff<0) or (VTI_diff*prev_VTI_diff<0):
                                    T_diff_sign_change += 1
                        if T_reached<0:
                            T_reached=0
                        if T_stopped<0:
                            T_stopped=0
                        if T_diff_sign_change<0:
                            T_diff_sign_change=0
                        prev_probe_T = probe_T
                        prev_VTI_T = VTI_T
                        prev_probe_diff = probe_diff
                        prev_VTI_diff = VTI_diff

                        if (T_reached >= 30) and (T_diff_sign_change>=5) and (time()-time0 > min_time):
                            measuring=False
                            print(f"Finished ramping {controller} to {T} K")
                            break
                        if (T_stopped >= 50) and (T_diff_sign_change>=5) and (time()-time0 > min_time):
                            measuring=False
                            print(f"Reached base T in {controller} at {probe_T} K probe, {VTI_T} K VTI")
                            break
                        if time()-time0 > timeout:
                            measuring=False
                            print("Timeout reached")
                            break
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise
    
    def set_T(self,controller,T,**kwargs):
        """Sets the temperature and records data continuously to a file.
        
        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end.
        controller : str
            The temperature controller to ramp.
            Must be either 'probe', 'VTI', or 'both'.
        T : float or list of floats or numpy array of floats
            The setpoint temperature(s) in K.
        **kwargs : dict
            Keyword arguments to pass to ramp_T. See ramp_T for more details.
        
        Returns
        -------
        None
            Data is written to a file.
        """
        self.ramp_T(controller,T,0,**kwargs)
        return
    
    def ramp_heater(self,probe_heater,VTI_heater,wait=0.1):
        """Ramps the heaters and records one datapoint per setpoint to a file.

        The heater power is iterated through the list of setpoints and the 
        temperature is recorded at each setpoint. There is no continuous ramping
        of the heaters whilst recording data. For a slower ramp, use a long wait
        time or small heater power step.

        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end.
        probe_heater : float or list of floats or numpy array of floats
            The setpoint heater power(s) for the probe in %.
        VTI_heater : float or list of floats or numpy array of floats
            The setpoint heater power(s) for the VTI in %.
        wait : float, optional
            The time to wait between measurements in seconds. Default is 0.1.
        comment : str, optional
            A comment to write to the file.

        Returns
        -------
        None
            Data is written to a file.
        """
        try:
            probe_heater=self.make_list(probe_heater)
            VTI_heater=self.make_list(VTI_heater)
            if len(probe_heater)==1:
                probe_heater = probe_heater*len(VTI_heater)
            if len(VTI_heater)==1:
                VTI_heater = VTI_heater*len(probe_heater)
            if len(probe_heater)!=len(VTI_heater):
                    print("WARNING: Probe and VTI heater lists area a different length")

            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                print("Ramping heaters")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],[f"Set Vg"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])

                time0=time()
                for probe_heat,VTI_heat in zip(probe_heater,VTI_heater):
                    self.iTC.set_probe_heater(probe_heat)
                    self.iTC.set_VTI_heater(VTI_heat)
                    sleep(wait)
                    data = self.read_everything(time0=time0)
                    writer.writerows([data])
                    f.flush()
                print(f"Finished ramping heaters")
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise

    
    def ramp_B(self,Bs,rates,threshold=0.005,timeout_hours=18):
        """Ramps the magnetic field and records data continuously to a file.

        The ramp is considered complete when the magnetic field is within the
        threshold of the setpoint (magnetic field is within setpoint +- 
        threshold for for 10 consecutive measurements).

        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end.
        Bs : float or list of floats or numpy array of floats
            The setpoint magnetic field(s) in T.
        rates : float or list of floats
            The ramp rate(s) in T/min.
        threshold : float, optional
            The threshold within which magnetic field to be considered at the 
            setpoint. The default is 0.005 T.
        timeout_hours : float, optional
            The number of hours to measure before the measurement times out. 
            Useful in case the set point is never reached. The default is 18. 
            The timeout is reset for each setpoint.
        comment : str, optional
            A comment to write to the file header.

        Returns
        -------
        None
            Data is written to a file.
        """
        try:
            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],[f"Ramp magnetic field"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])

                Bs = self.make_list(Bs)
                rates = self.make_list(rates)
                if len(rates)==1:
                    rates=rates*len(Bs)
                if len(Bs) != len(rates):
                    print("Warning: length of B and rate lists are not equal")

                for B,rate in zip(Bs,rates):
                    min_time = 60*abs(B-self.iPS.get_field())/rate
                    self.iPS.set_field(B,rate)
                    print(f"Ramping magnet to {B} T at {rate} T/min")

                    time0 = time()
                    timeout=timeout_hours*3600

                    B_reached = 0
                    measuring = True
                    while measuring:
                        data = self.read_everything(time0=time0)
                        writer.writerows([data])
                        f.flush()
                        sleep(0.01)

                        if abs(self.iPS.get_field()-B) < threshold:
                            B_reached += 1
                        else:
                            B_reached = 0

                        if (B_reached >= 10) and (time()-time0 > min_time):
                            measuring=False
                            print(f"Finished ramping magnet to {B} T")
                            break
                        if time()-time0 > timeout:
                            measuring=False
                            print("Timeout reached")
                            break
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise
    
    def reset_Vg(self):
        for _,Vsourcemeter in self.Vsourcemeters:
            Vsourcemeter.reset()
    
    def set_Vg(self,Vgs,compliance=5e-7,wait=0.1):
        """
        Sets the gate voltage and records one datapoint per setpoint to a file.
        
        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same 
            name already exists, a number will be appended to the end of the 
            filename.
        Vgs : float or list of floats or numpy array of floats
            The setpoint gate voltage(s) in V.
        compliance : float, optional
            The compliance current in A. The default is 5e-7.
        wait : float, optional
            Time to wait between measurements in seconds. The default is 0.1.
        comment : str, optional
            A comment to write to the file. The default is " ".
                
        Returns
        -------
        None
            Data is written to a file.
        """
        Vgs=self.make_list(Vgs)
        if any([Vg>250 for Vg in Vgs]):
            print("Gate setpoints exceed 250 V")
            return
        try:
            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                print("Setting gate voltages")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],[f"Set Vg"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])

                for _,Vsourcemeter in self.Vsourcemeters:
                    Vsourcemeter.set_compliance(compliance)
                    Vsourcemeter.set_voltage(Vgs[0])
                    Vsourcemeter.turn_on()

                time0=time()
                for Vg in Vgs:
                    for _,Vsourcemeter in self.Vsourcemeters:
                        Vsourcemeter.set_voltage(Vg)
                        sleep(wait)
                        data = self.read_everything(time0=time0)
                        writer.writerows([data])
                        f.flush()
                print("Finished setting gate voltages")
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise

    def set_current(self,I,compliance=5,on=[True]):
        """Sets the current without recording data.

        Parameters
        ----------
        I : float
            The setpoint current in A. The maximum allowed is 1e-4 A.
        compliance : float, optional
            The compliance current in A. The default is 5e-7.

        Returns
        -------
        None
        """
        I = self.make_list(I)
        if max(I) >= 1e-4:
            print(f"Current setpoint {I} A is larger than max 1e-4 A")
            return
        compliance = self.make_list(compliance)
        on = self.make_list(on)
        if len(I)==1:
            I=I*len(self.sourcemeters)
        if len(compliance)==1:
            compliance=compliance*len(I)
        if len(I) != len(compliance):
            print("Warning: length of I and compliance lists are not equal")
        if len(on)==1:
            on=on*len(I)
        if len(I) != len(on):
            print("Warning: length of I and on/off lists are not equal")
        
        print(f"Setting current to {I}, compliance {compliance}")
        for i in range(len(self.sourcemeters)):
            self.sourcemeters[i][1].set_compliance(compliance[i])
            self.sourcemeters[i][1].set_current(I[i])
            if on[i]:
                self.sourcemeters[i][1].turn_on()
            else:
                self.sourcemeters[i][1].turn_off()
    
    def perform_IV(self,Is,compliance=5,wait=0.01):
        """Changes the current, records one datapoint per setpoint to a file.
        
        Parameters
        ----------
        filename : str
            The name of the file to write the data to. If a file with the same
            name already exists, a number will be appended to the end of the 
            filename.
        Is : float or list of floats or numpy array of floats
            The setpoint current(s) in A. The maximum allowed is 1e-4 A.
        compliance : float, optional
            The compliance current in A. Default is 5e-7.
        wait : float, optional
            The time to wait between measurements in seconds. Default is 0.1.
        comment : str, optional
            A comment to write to the file header.
                
        Returns
        -------
        None
            Data is written to a file.
        """
        try:
            with ConditionalFileWriter(self.filename,self.measure) as f:
                if self.measure:
                    print(f"Writing data to {self.filename}")
                else:
                    print("Not writing data to file")
                print("Performing IV measurement")
                writer = csv.writer(f)
                writer.writerows([[str(ctime())],[f"Measure IV"],[self.comment],["[DATA]"]])
                headers = self.get_headers()
                writer.writerows([headers])
                
                for _,sourcemeter in self.sourcemeters:
                    sourcemeter.set_compliance(compliance)
                    sourcemeter.set_current(Is[0])
                    sourcemeter.turn_on()
                
                time0=time()
                for I in Is:
                    if abs(I)<=1e-4:
                        for _,sourcemeter in self.sourcemeters:
                            sourcemeter.set_current(I)
                        sleep(wait)
                        data = self.read_everything(time0=time0)
                        writer.writerows([data])
                        f.flush()
                    else:
                        print(f"Current setpoint {I} A is larger than max 1e-4 A")
                print("Finished IV measurement")
            return
        except KeyboardInterrupt:
            print("User interrupted measurement")
            self.flush_and_reset()
            raise