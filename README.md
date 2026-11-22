# BUMRC Electronics Scanner

Inventory scanner for the Boston University Mars Rover Club. A USB camera on a
Jetson Nano reads the barcodes on distributor bags (DigiKey, Mouser, Newark,
Vishay), looks the part up on DigiKey, uploads it to the club's inventory
website, and prints an inventory label on a Brother QL-820NWB.

The app runs in Docker and is shown full screen in Chromium on the Jetson's monitor.

## Hardware

- NVIDIA Jetson Nano 4GB (Auvidea carrier board), Ubuntu 18.04 / JetPack 4.x
- Arducam UC-A52 8MP USB camera (3264x2448)
- Brother QL-820NWB label printer over USB, 62 mm red/black ("62red") labels
- Monitor, keyboard and mouse

## Jetson setup

The Jetson needs internet access, and the code needs to be on GitHub.

### 1. Docker

Check the versions:

    docker --version          # needs 20.10.10 or newer
    docker compose version    # needs Compose v2

If `docker compose` is not found, install the Compose plugin:

    mkdir -p ~/.docker/cli-plugins
    curl -SL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-aarch64 \
         -o ~/.docker/cli-plugins/docker-compose
    chmod +x ~/.docker/cli-plugins/docker-compose

Let your user run Docker without sudo, then log out and back in:

    sudo usermod -aG docker $USER

### 2. Check the camera and printer

Plug both in and switch the printer on.

    sudo apt install v4l-utils
    v4l2-ctl --list-formats-ext     # should list MJPG 3264x2448
    ls /dev/usb/lp*                 # should show /dev/usb/lp0

If `/dev/usb/lp0` is missing, load the USB printer driver and make it load at boot:

    sudo modprobe usblp
    echo usblp | sudo tee -a /etc/modules

### 3. Get the code and configure it

    git clone <repository URL> ~/inventory-scanner
    cd ~/inventory-scanner
    mkdir -p data                   # must exist before the first start (Docker would create it as root)
    cp .env.example .env
    nano .env                       # fill in the values, see ".env settings" below

If your user ID is not 1000 (check with `id -u`), add `UID=<your id>` and
`GID=<your group id>` to `.env`.

### 4. Build and start

    docker compose up -d --build

The first build downloads about 300 MB and takes a few minutes. The app then
starts on its own at every boot. Open http://localhost:8080 to see it.

### 5. Check everything works

    docker compose run --rm scanner python -m scanner.check

Every line should say `ok`. This uploads nothing and prints nothing. Then run the tests:

    docker compose run --rm scanner python -m pytest -q

### 6. Full-screen kiosk at login

    sudo apt install chromium-browser
    mkdir -p ~/.config/autostart
    cp deploy/bumrc-kiosk.desktop ~/.config/autostart/

Turn on automatic login (Settings > Users > Automatic Login, or set
`AutomaticLoginEnable=true` and `AutomaticLogin=<user>` in `/etc/gdm3/custom.conf`),
then reboot. The scanner opens full screen. Alt+F4 closes it.

The kiosk script expects the code in `~/inventory-scanner`. If it is somewhere
else, edit the `Exec=` line in `~/.config/autostart/bumrc-kiosk.desktop`.

### 7. First use: calibrate the camera

On the Camera tab, open the camera and hold a bag label about where it will be
when scanning. Press Auto calibrate (about a minute), then Save settings. Boxes
on the live view show which codes are being read.

## Updating

    cd ~/inventory-scanner
    git pull
    docker compose up -d --build

## .env settings

| Setting | What it is |
|---|---|
| `LABEL_PRINTER` | Printer location. Default `file:///dev/usb/lp0` (USB). |
| `CAMERA_DEVICE` | Camera. Default `/dev/video0`. `file:references/captures` uses test images instead. |
| `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | From an app created at developer.digikey.com (Product Information API). |
| `INVENTORY_API_URL` | Full URL of `api/items.php` on the inventory website. |
| `INVENTORY_API_KEY` | Must match `api_key` in the website's `config.php`. |
| `DATA_DIR` | Where settings, captures and label images go. Set to `/data` by Docker; leave unset. |
| `UID`, `GID` | Only if your Jetson user ID is not 1000. |

## Troubleshooting

| Problem | Fix |
|---|---|
| See what the app is doing | `docker compose logs -f` |
| "can't open camera" | Another program has it open, or it's unplugged. Check `ls /dev/video*`, then `docker compose restart`. |
| "label printer not found" | Printer off or unplugged, or `usblp` not loaded (step 2). |
| "no permission to use /dev/usb/lp0" | Check `ls -l /dev/usb/lp0` is group `lp`. The container joins groups `video` (44) and `lp` (7). |
| Codes not decoding | Re-run Auto calibrate on the Camera tab. Use Save capture to keep a hard example for testing. |
| Kiosk doesn't open | Run `~/inventory-scanner/deploy/kiosk.sh` in a terminal to see the error. |
| Running out of memory | `free -h`. Close other programs; see below. |

## Saving RAM (optional)

The Jetson uses about 1.3 GB at rest; the app and Chromium add roughly 0.5-0.9 GB.
If memory is tight, a lighter desktop helps the most:

    sudo apt install lxde
    # choose "LXDE" on the login screen (gear icon), and set it for automatic login

## Development on a PC

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    cp .env.example .env                                     # fill in as above

    CAMERA_DEVICE=file:references/captures .venv/bin/python -m scanner   # no camera needed
    .venv/bin/python -m scanner                                          # real camera
    .venv/bin/python -m pytest -q                                        # tests (offline)
    .venv/bin/python -m pytest -q -m network                             # live DigiKey test

Then open http://localhost:8080. Careful: Upload sends to the real server in `.env`.

Test images live in `references/captures/`. Each `.png` has a `.json` file with
the values it should decode to; the tests check every one automatically.
Captures saved from the app's Camera tab go to `data/captures/`. Copy them into
`references/captures/` and commit them to keep them as tests.

## Project layout

    scanner/
      camera/     camera access, focus/sharpness controls, calibration
      decode/     barcode reading, label parsing, code boxes
      lookup/     DigiKey API and filling in part details
      labels/     label layout and printing (layout must not change)
      web/        the local web interface (FastAPI + plain HTML/JS)
      scan.py     one "Scan bag" press
      inventory.py  upload client for the website API
      check.py    setup checker
    tests/        tests and saved DigiKey responses
    references/   example captures, original printer code, current website UI
    deploy/       kiosk startup script
