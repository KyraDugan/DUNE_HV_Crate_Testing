import pyvisa


class Agilent34970A:
    def __init__(self, slot=1, channels=None, gpib_resource="3923:709b", nplc=10):
        """
        slot: 34901A slot number (1, 2, or 3)
        channels: list of channel numbers on that slot, e.g. [1,2,3,4]
        gpib_resource: optional explicit VISA resource string
        nplc: integration time (noise vs speed)
        """

        if channels is None:
            channels = [1, 2, 3, 4,5]

        self.slot = slot
        self.channels = channels
        self.nplc = nplc

        self.rm =rm = pyvisa.ResourceManager('/usr/lib/x86_64-linux-gnu/libvisa.so') 

        # Auto-detect GPIB resource if not provided
        if gpib_resource is None:
            resources = self.rm.list_resources()
            gpib_list = [r for r in resources if "GPIB" in r]
            if not gpib_list:
                raise RuntimeError("No GPIB instrument found.")
            self.resource = gpib_list[0]
        else:
            self.resource = gpib_resource

        self.inst = self.rm.open_resource('GPIB0::PAD::INSTR')
        self.inst.timeout = 10000
        self.inst.write_termination = "\n"
        self.inst.read_termination = "\n"

        self._configure()

    # -------------------------
    # Private configuration
    # -------------------------
    def _configure(self):
        self.inst.write("*RST")
        self.inst.write("*CLS")

        # Build channel list (@101,102,...)
        ch_list = ",".join(f"{self.slot}{ch:02d}" for ch in self.channels)
        self.ch_list = f"(@{ch_list})"

        # Configure DC voltage measurement
        self.inst.write(f"CONF:VOLT:DC {self.ch_list}")
        self.inst.write(f"ROUT:SCAN {self.ch_list}")

        self.inst.write(f"VOLT:DC:NPLC {self.nplc}")

        self.inst.write("TRIG:SOUR IMM")
        self.inst.write("TRIG:COUN 1")

        self.inst.write("FORM:READ:CHAN ON")
        self.inst.write("FORM:READ:TIME OFF")
        self.inst.write("FORM:READ:UNIT OFF")

    # -------------------------
    # Read one scan
    # -------------------------
    def read(self):
        """
        Perform one scan and return dict:
        {channel_number: voltage}
        """
        resp = self.inst.query("READ?")

        parts = [p.strip() for p in resp.split(",") if p.strip()]
        pairs = list(zip(parts[0::2], parts[1::2]))

        result = {}
        for val, ch in pairs:
            result[int(ch)] = float(val)

        return result

    # -------------------------
    # Close connection
    # -------------------------
    def close(self):
        self.inst.close()
        self.rm.close()

if __name__ == "__main__":
    dmm = Agilent34970A(slot=1, channels=[1, 2, 3, 4,5], nplc=10)

    data = dmm.read()
    print (data)
    print("Scan result:")
    for ch, val in data.items():
        print(f"  CH{ch}: {val:.6f} V")
#    data = dmm.read()
#    for ch, val in data.items():
#        print(f"  CH{ch}: {val:.6f} V")

    dmm.close()


