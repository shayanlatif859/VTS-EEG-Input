# VTube-Studio-EEG-Input

A real-time EEG → VTube Studio interface using Muse headbands, BrainFlow, OSC, and spectral band analysis.

This project allows EEG activity to drive avatar expressions, hotkeys, and live model parameters inside VTube Studio using configurable rule-based mappings. It is meant for use with consumer grade EEG devices, specifically 
the MUSE 2 and MUSE S headband, which have four EEG channels. By extracting spectral EEG activity in real time, the system can respond dynamically to changing physiological states such as relaxation, fatigue, or heightened arousal.

The goal is not emotion detection, but interactive experimentation with physiological state-driven animation and control. EEG-derived states are interpreted heuristically using spectral activity patterns and configurable rule mappings.
## Features
- Live Muse EEG streaming via BrainFlow
- Synthetic EEG fallback if Muse not connected
- CSV replay support
- Real-time delta/theta/alpha/beta/gamma extraction
- Rolling normalization
- OSC output
- Rule-based interpretation system
- VTube Studio integration
- Expression/hotkey/parameter control VIA JSON file
- Real-time terminal dashboard

## Basic functionality
### EEG Acquisition + Sliding buffer analysis
A stream for a discrete-time signal is created for each EEG channel, which may be represented as $ x[n] $, with $ x= $ EEG amplitude and $ n= $ the sample index. This data is stored and a sliding buffer 4 seconds long is set to 
be taken at every sample so that it may analyze frequencies from 0.5 Hz samples (delta waves).
In Hz, the common frequency ranges of the brain are defined as:
- Delta ~ 0.5-4
- Theta ~ 4-8
- Alpha ~ 8-12
- Beta ~ 13-30
- Gamma ~ 30-45

### Bandpass Filtering
All samples from each electrode are singled out so that each electrode may be applied with a Butterworth bandpass filter, which has a flat frequency response in the passband, minimizing the effects of ripples and artifacts.
This allows for the separation of the raw EEG signal in the channel into the five frequencies ranges.

Suppose the signal resembled this:

$$
x(t)=\sin(2\pi 3t)+\sin(2\pi 10t)+\sin(2\pi 25t)
$$

This contains 3 Hz delta, 10 Hz alpha, and 25 Hz beta. Applying an alpha filter would filter out the signal to output:

$$
y(t)\approx\sin(2\pi 10t)
$$

It is then filtered back and forth to prevent phase distortion using 'filtfilt()'.

### Power Estimation
The power is then computing squaring the oscillating signal, turning it into a positive magnitude only, then applying the discrete formula:

$$
P = \frac{1}{N}\sum_{n=1}^{N} x[n]^2
$$

The power is then added and averaged across bands to get a rough estimate of the band strength in the brain.

$$
P_{band}=\frac{1}{C}\sum_{c=1}^{C} P_c
$$

...where $C=$ number of channels.

This data is still raw EEG input, so it is not stable, thus it has to be normalized.
### Normalization
Using a 30-second window and getting the minimum and maximum, the 
value is normalized with the equation for min-max normalization, which maps values from $0≤x_{norm}≤1$. The equation is as follows:

$$
x_{norm}=\frac{x-x_{min}}{x_{max}-x_{min}}
$$

This normalized value may be displayed and, then received by the bridge, which will stream to VTS and using a JSON file mapping these float values to rules, will interpet the rules accordingly.

## Installation
Type this into the terminal for all the libraries. Ensure pip is installed.
```bash
pip install numpy scipy python-osc pylsl websocket-client brainflow
```
Then download the VTS application, enable API settings, and run bridge_average.py in the terminal.
```bash
python bridge_average.py
```
Then, go back to the app, approve the connection. Now, you may run musesimulator_average to either connect the MUSE device:
```bash
python input_and_simulator_average.py --verbose
```
Run a synthetic board:
```bash
python input_and_simulator_average.py --verbose --synthetic
```
Or run a CSV file from either Muse Direct or Muse Monitor:
Run a synthetic board:
```bash
python input_and_simulator_average.py --csv '{CSV DIRECTORY HERE}' --verbose  
```

## Current Limitations

- Current implementation averages spectral power across electrodes
- Artifact rejection is minimal
- Consumer EEG devices are noisy and low-channel-count
- State interpretation is heuristic and rule-based
- GUI configuration tools are still in development


Note that this program is far from complete. We must add a proper GUI and a way to measure all electrodes and return each individually in a manner that does not cause data leaks. Allowing for each electrode to stream information
rather than an average will let us get the frequency values in each part of the brain rather than as an average, allowing for more accurate measures of conditions.
Consider reading into the effect of EEG states on the brain:

https://pmc.ncbi.nlm.nih.gov/articles/PMC8777059/ ~ Jiang L, Siriaraya P, Choi D, Kuwahara N. Emotion Recognition Using Electroencephalography Signals of Older People for Reminiscence Therapy. Front Physiol. 2022 Jan 7;12:823013. doi: 10.3389/fphys.2021.823013. PMID: 35069270; PMCID: PMC8777059.


https://pmc.ncbi.nlm.nih.gov/articles/PMC12384336/ ~ Serna B, Salazar R, Alonso-Silverio GA, Baltazar R, Ventura-Molina E, Alarcón-Paredes A. Fear Detection Using Electroencephalogram and Artificial Intelligence: A Systematic Review. Brain Sci. 2025 Jul 29;15(8):815. doi: 10.3390/brainsci15080815. PMID: 40867148; PMCID: PMC12384336.


https://pmc.ncbi.nlm.nih.gov/articles/PMC11219808/ ~ Redwan SM, Uddin MP, Ulhaq A, Sharif MI, Krishnamoorthy G. Power spectral density-based resting-state EEG classification of first-episode psychosis. Sci Rep. 2024 Jul 2;14(1):15154. doi: 10.1038/s41598-024-66110-0. PMID: 38956297; PMCID: PMC11219808.

## Future Work

- Per-electrode spectral outputs
- Frontal asymmetry analysis
- Relative band power metrics
- Artifact rejection and blink filtering
- PyQt configuration interface
- Temporal smoothing and hysteresis






