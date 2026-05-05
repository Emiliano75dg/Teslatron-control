# Teslatron controller

This Python tool is made for controlling the Teslatron system at the University of Geneva Department of Quantum Materials Physics.

The aim of the tool is to extend the capabilities of the instrument beyond the existing labview program by:
- allowing simultaneous measurement from many voltmeters
- allowing temperature sweeps by sweeping heater power directly
- giving the ability to write more complicated measurement programs through a script

## How to use

Currently the best way to control the Teslatron is to write a measurement script as a .py or (preferably) .ipynb Jupyter notebook file.

Install Python 3, then numpy, pyvisa, and notebook. For handling and plotting the data afterwards, pandas and matplotlib are useful:
```
pip install numpy pyvisa notebook pandas matplotlib
```
For PyVisa to work, you will need to install the [National Instruments VISA library](https://pyvisa.readthedocs.io/en/latest/faq/getting_nivisa.html#faq-getting-nivisa).

Clone this repository, and see the example_measurement_script.ipynb to see how one can write and execute a measurement script on the Teslatron system.

Copyright (c) 2024 Graham Kimbell
