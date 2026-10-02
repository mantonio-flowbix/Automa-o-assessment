FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Headless Chromium + its system libs, used to screenshot the Zabbix frontend
# for the PPTX evidence slides (flowbix_assess/collectors/zabbix_screenshots.py).
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 5050

CMD ["python", "-m", "webapp.app"]
