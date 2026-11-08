# Show received chat on the UNO Q matrix

This overlay is for the App Lab app that already runs Mycelium in the
`mycelium_peer` Custom Brick. Keep that Brick's D-Bus mount. Add this volume and
environment variable to the existing `mycelium_peer` service:

```yaml
volumes:
  # Keep the existing D-Bus volume and add this entry.
  - /home/arduino/ArduinoApps/mycelium/python:/matrix
environment:
  # Keep DBUS_SYSTEM_BUS_ADDRESS and add this entry.
  MYCELIUM_MATRIX_FILE: /matrix/.mycelium-matrix.json
```

1. Copy the updated `Backend/peer.py` into the Brick's Docker build context.
2. Copy `python/main.py` and `sketch/sketch.ino` from this overlay into the
   corresponding folders of the App Lab app. If your sketch already has other
   behavior, integrate the display handler into its existing setup and loop.
3. Add ArduinoGraphics to the sketch libraries in App Lab. The UNO Q core
   provides Arduino_LED_Matrix. Keep Arduino_RouterBridge in the sketch libraries.
4. Run the app with the updated Brick image and send `HELLO` from another peer.

Only incoming chat addressed to this node or broadcast is shown. The shared
file holds the latest message, so bursts can overwrite an unread message.
The matrix renders printable ASCII and replaces unsupported characters with
question marks.
