FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY ble.py peer.py protocol.py ./
CMD ["python3", "-u", "ble.py", "run"]