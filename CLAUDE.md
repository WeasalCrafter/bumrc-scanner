# BUMRC Electronics Scanner App

## Preamble

You are a fullstack developer developing a Python application

Your application runs on a 4GB Jetson Nano with an Auvidea carrier board

At rest, the jetson has about 1.3GB in use

## Project Description

The purpose of this application is inventory management for the Boston University Mars Rover Club. The 
idea is to have a desktop scanner to inventory electronics components to a web hosted database. The system 
prints an internal inventory barcode on it. Then when a user wants to add/remove a component, they scan the internal ID, 
either on the desktop scanner or their mobile device (more on that below) and enters the add/remove amount.

## Detailed Project Description

It will use a USB Arducam to scan barcodes on bags from:
 - Digikey
 - Mouser
 - Vishay
 - Newark

And possibly more.

The application should scan the bags, determine what they are: (scanning barcodes and datamatrix)
 - Find the MPN
 - Find the original qty
 - Distributor
 - Attempt to find the product on digikey (link to digikey listing)
   - Fallback to finding product on original distributors website
 - Attempt to find datasheet link
 - Any details of the components inside

And take this info and add it to an internal BUMRC Sqlite database hosted online.

For internal purposes, each new bag will be given an internal ID, this will be printed on a barcode automatically using a Brother QL Printer.

The barcode design is created and you will have access to the code that generates the proper output (it uses brother ql next).

You will also have a folder of example captures to test decoding with.

# List of Hardware

- Arducam USB Camera over usb (UC-A52), 8MP, max resolution 3264x2448. Scan at full resolution; preview at reduced resolution. With the right focus and sharpness it decodes almost every bag, so the reference captures (cropped and not always in focus) are a worst case.
- Nvidia Jetson Nano (4GB) running on Auvidea carrier board
    - At rest the Jetson uses 1.3GB of ram, maybe this can be minimized.
    - The jetson is running Ubuntu 18.04.6-LTS, linux kernel 4.9.253-Tegra
    - Running python 3.6
- Brother QL-820NWB using "62red" printer paper over usb

## Deployment

- The scanner app runs in a Docker container on the Jetson, started with Docker Compose. The host's Python 3.6 is not used; do not modify it.
- Base image: arm64 `python:3.12-slim-bookworm` (Debian; numpy 2.5 needs Python 3.12+). Install compiled libraries from wheels or apt inside the image. No CUDA/JetPack base image is needed, since barcode decoding is CPU-only and the camera is read over V4L2.
- Pass the camera (`/dev/video0`) and printer (`/dev/usb/lp0` or `/dev/bus/usb`) into the container. Use host networking.
- Mount volumes for the calibration settings file, the label output folder, and `.env`.
- The UI is a web interface served by the container. Chromium on the Jetson opens it full-screen (kiosk mode) at startup. If RAM gets tight, a lighter browser (WebKit-based or Firefox) can replace Chromium without changing the app code. The local UI is built with FastAPI (served by uvicorn) and plain HTML/CSS/JS (htmx or Alpine.js allowed for interactivity). No NiceGUI and no Node build step. Match the styling of the web UI in `references/current UI` so both interfaces look consistent. The camera preview is served as an MJPEG stream shown in an `<img>` tag.
- Stream the camera preview to the browser at reduced resolution and frame rate (about 640x480 at 10-15 fps). Decode barcodes on full-resolution frames.
- Host requirement: Docker 20.10.10 or newer (the Jetson has 20.10.21, OK). Older Docker blocks system calls (e.g. clone3) that newer Debian images need, which breaks threads inside the container.
- The README must document `docker compose up`, the device mappings, and the kiosk autostart.

## File information

This project is intended to be formatted as a Github repo so include a readme without emojis explaining how to setup in a concise way

There is a folder called `references`, which includes the following:
 - `captures` - contains images taken from the arducam of examples of bags to inventory
 - `printer code` - contains code used to print from a raspberry pi to the label printer via usb, this contains the desired format for the internal labels
 - `current UI` - contains the current web user interface this should be based on (`styles/main.css1` is an old copy; ignore it)

Make sure to create a proper gitignore file

## Web Information

The web side of this project is hosted on a webserver that has FTP and PHP with https. It should be an SQLite database that has as many fields as the barcodes can 
encode, within reason, as to prevent waste of storage.

The web UI is designed and will be in the references folder. Making a simple PHP API pathway is likely the best way for this project to proceed.

The user should be able to scan the internal barcodes when on the web interface on mobile or on a laptop using their own camera. The user interface 
should then prompt them to enter a quantity of which they are removing/adding, then this should reflect on the database.

The web interface does not need a way to manually add a component as of now.

The web interface should need a simple, but styled login (styled as the rest of the UI), with a single shared password (no per-user accounts) that does not need to be super encrypted.

Quantities can never go negative. The API must reject a removal larger than the current stock. When a quantity is 0, display it in red.

You will have access to digikey's API, and if needed, we will setup Mouser's API

The scanner app authenticates to the PHP API with a shared API key, stored in the .env file (on both the scanner and server side).

Leave instructions for how to make the .env file, but it will be gitignored

For having pdo_sqlite, i tested:
 - var_dump(in_array('sqlite', PDO::getAvailableDrivers(), true));

and it returned true.

## Local Interface

There should be one application interface for this. It is a web interface built with FastAPI and plain HTML/CSS/JS (see Deployment), chosen over NiceGUI to keep RAM use low on the Jetson.

The app runs directly on the Jetson, which has a monitor, keyboard, and mouse attached.

The left side will be for previews, camera feeds, etc. The right side for fields and typing portions.

The app has a few features
 - Camera calibration: Button that opens camera feed, sweeps through focus and sharpness to decode the most amounts of barcodes (also have option for manual tune) and be able 
to save to a local file that the app pulls settings from. Closes camera when done.
    - Also has a "Save capture" button that saves the current full-resolution frame to a captures folder (a mounted Docker volume) with a JSON sidecar file containing a user-entered description, the distributor, a timestamp, and the decoded fields (which the user can confirm or correct). These captures are used as test cases for the decoder; there is no ML training. The decoder test suite must run over every capture in the folder automatically, so new captures can be added at any time without code changes.

 - Product Scanning: 
    - have a button that when pressed, opens the camera and scans for a short period to find all the codes in the screen. 
    - Then, once it has the codes, search for the aforementioned fields above (MPN, qty, etc.)
    - It then shows the user on the right side of the screen the fields all filled in, and prompts to check/correct any missing
    - If desired, the user can rescan or change the fields. Then they can press, Upload.
    - This will send a request to the webserver to upload this field to the Database, and the database should have a clear response of success or not
    - If the upload fails (server error, no network, etc.), show a clear error to the user. Do not queue uploads locally.
    - Duplicate bags (same MPN/lot scanned again) are always created as a new entry with their own unique ID.
    - The database should also respond with the ID of the new component bag.
    - With this, the app displays a preview of the label that will be printed. It allows the user to type in a different description if needed. The ID number cannot be changed after upload.
    - Then, the user clicks print and the printer is sent the command to make this label. It also saves the label as an image in an output folder (only keep last 5) in case the label maker doesnt print
    - In case the printer needs a second print, keep the label on the screen until the user clicks finish to indicate they are done with printing out the label.
    - The app now should tell the user that they can move on and the product has been successfully inventoried.

## Design Flow

This should not be designed in one go, make it in chunks

First, make the local application for scanning and decoding and adapt the printing code to hook into it (the printing code can be changed, but the layout of the label must not). The one approved change: the ID is printed and encoded in the barcode as "BUMRC-XXXXXX" (6-digit, zero-padded), with no "ID:" text. The phone scanner must accept this format.

Then once this is complete, we move on to the database and inventory web ui. For now, ignoring the user scanner portion of the web ui.

Once the scanner app can communicate with the website correctly, and products can be inputted, labels are printed correctly, then we move on.

We move on to the web component of a user scanning the internal IDs with their phone to remove/add components.



Do as many tests as you can, if you cannot automatically test, prompt the user to test. This code will likely be written on one PC and transferred to the Jetson via Github.
