### Work Instruction
1. Input the serial number and PO.
2. Attach the USB charging cable to the device.
3. Press the button on the device. White LEDs will turn on and slowly disappear. Wait until the blue LEDs are flashing.
4. Put the device on the jig, with the button facing the camera.
5. Start the test.

### Version History
| Version | Changes |
| :------: | :----------- |
| 1.0.0 | <ul><li>Initial deployment</li></ul> |
| 1.1.0 | <ul><li>Added retries for HTTP requests</li><li>Auto-fail timeout (45 seconds of DUT inactivity) </li></ul> |
| 1.2.0 | <ul><li>Removed DAQ</li><li>Increased HTTP retries</li></ul> |
| 1.2.1 | <ul><li>Reconnect to database before and after test</li></ul> |
| 1.2.2 | <ul><li>Placed login inside Settings</li><li>Low and high limits included in the table</li><li>Check AP connection every 5 seconds</li></ul> |
| 1.3.0 | <ul><li>BT-Wifi moved to separate thread</li><li>Re-added LAN</li></ul> |