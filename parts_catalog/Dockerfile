# HA's builder passes BUILD_FROM; this add-on uses the official multi-arch Python image instead (no s6, init: false).
ARG BUILD_FROM
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY . /tmp/src
# If the build folder carries pre-downloaded wheels (a packaging script, not part of this repository, adds them
# for HA installs whose build network has no DNS), install offline; otherwise from PyPI.
RUN cd /tmp/src \
 && if [ -d wheels ]; then pip install --no-cache-dir --no-index --find-links wheels -r requirements.txt; \
    else pip install --no-cache-dir -r requirements.txt; fi \
 && mkdir -p /opt/parts-catalog \
 && cp -r app static run_addon.py /opt/parts-catalog/ \
 && if [ -d tools ]; then cp -r tools /opt/parts-catalog/; fi \
 && rm -rf /tmp/src
WORKDIR /opt/parts-catalog
CMD ["python", "run_addon.py"]
