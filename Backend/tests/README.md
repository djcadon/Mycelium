# Backend tests

Run the backend unit and local integration tests from the repository root:

```powershell
python -m unittest discover -s Backend/tests -v
```

These tests use local fakes and loopback sockets; they do not require UNO Q
hardware. Hardware-dependent mesh checks should be run separately.
