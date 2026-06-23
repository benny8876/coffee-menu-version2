/**
 * Bluetooth thermal printer support for Android Chrome (Web Bluetooth API).
 * Renders receipts as raster images so Myanmar Unicode prints correctly.
 */
(function (global) {
    const ESC = '\x1B';
    const GS = '\x1D';
    const PRINTER_WIDTH = 384;
    const PADDING_X = 12;
    const FONT_FAMILY = '"Noto Sans Myanmar", sans-serif';
    const DEFAULT_FONT_SIZE = 22;
    const LINE_HEIGHT = 30;
    const RASTER_BAND_HEIGHT = 24;

    const PRINTER_PROFILES = [
        {
            name: 'Generic BLE thermal',
            service: '000018f0-0000-1000-8000-00805f9b34fb',
            write: '00002af1-0000-1000-8000-00805f9b34fb',
        },
        {
            name: 'Nordic UART style',
            service: '6e400001-b5a3-f393-e0a9-e50e24dcca9e',
            write: '6e400002-b5a3-f393-e0a9-e50e24dcca9e',
        },
        {
            name: 'HM-10 / SPP BLE',
            service: '49535343-fe7d-4ae5-8fa7-af26b7df8e01',
            write: '49535343-fe7d-4ae5-8fa7-af26b7df8e02',
        },
    ];

    const PRINTER_ID_KEY = 'bluetooth_printer_device_id';

    let bluetoothDevice = null;
    let writeCharacteristic = null;
    let currentReceipt = null;
    let lastStatusEl = null;
    let reconnectTimer = null;
    let reconnectInFlight = false;
    let fontReadyPromise = null;

    function formatMoney(amount) {
        return `${Number(amount).toFixed(0)} Ks`;
    }

    function ensureMyanmarFont() {
        if (!fontReadyPromise) {
            fontReadyPromise = (async () => {
                try {
                    await document.fonts.load(`700 24px ${FONT_FAMILY}`);
                    await document.fonts.load(`400 22px ${FONT_FAMILY}`);
                } catch (err) {
                    console.warn('Myanmar font preload failed; using system fallback.', err);
                }
            })();
        }
        return fontReadyPromise;
    }

    function setFont(ctx, size, bold) {
        ctx.font = `${bold ? '700' : '400'} ${size}px ${FONT_FAMILY}`;
    }

    function wrapText(ctx, text, maxWidth) {
        const value = String(text);
        const lines = [];
        let current = '';

        for (const char of value) {
            const candidate = current + char;
            if (ctx.measureText(candidate).width > maxWidth && current) {
                lines.push(current);
                current = char;
            } else {
                current = candidate;
            }
        }

        if (current) {
            lines.push(current);
        }

        return lines.length ? lines : [''];
    }

    function buildReceiptLayout(receipt) {
        const title = receipt.restaurant_name || '27 Cafe & Bar';
        const rows = [];

        rows.push({ type: 'text', text: title, align: 'center', bold: true, size: 26 });
        rows.push({ type: 'gap', height: 6 });

        if (receipt.voucher_id) {
            rows.push({ type: 'text', text: receipt.voucher_id, align: 'center', size: 20 });
        } else if (receipt.order_ids && receipt.order_ids.length) {
            rows.push({ type: 'text', text: 'TABLE SESSION INVOICE', align: 'center', size: 20 });
        }

        if (receipt.timestamp) {
            rows.push({ type: 'text', text: receipt.timestamp, align: 'center', size: 18 });
        }

        rows.push({ type: 'rule' });
        rows.push({ type: 'text', text: `Table: ${receipt.table_number}`, align: 'left' });

        if (receipt.order_ids && receipt.order_ids.length) {
            rows.push({
                type: 'text',
                text: `Orders: ${receipt.order_ids.map((id) => `#${id}`).join(', ')}`,
                align: 'left',
                size: 20,
            });
        }

        rows.push({ type: 'rule' });

        (receipt.items || []).forEach((item) => {
            const subtotal = item.subtotal != null
                ? item.subtotal
                : item.quantity * item.unit_price;
            rows.push({
                type: 'pair',
                left: `${item.name} x${item.quantity}`,
                right: formatMoney(subtotal),
            });

            (item.modifiers || []).forEach((mod) => {
                const modName = typeof mod === 'string' ? mod : mod.name;
                if (modName) {
                    rows.push({ type: 'text', text: `  + ${modName}`, align: 'left', size: 20 });
                }
            });
        });

        rows.push({ type: 'rule' });
        rows.push({
            type: 'pair',
            left: 'GRAND TOTAL',
            right: formatMoney(receipt.subtotal),
            bold: true,
            size: 24,
        });
        rows.push({ type: 'rule' });

        if (receipt.status) {
            rows.push({ type: 'text', text: `Status: ${receipt.status}`, align: 'left', size: 20 });
        }

        rows.push({ type: 'gap', height: 8 });
        rows.push({ type: 'text', text: 'Thank you!', align: 'center', bold: true });
        rows.push({ type: 'text', text: title, align: 'center', size: 20 });
        rows.push({ type: 'gap', height: 24 });

        return rows;
    }

    function measureReceiptHeight(ctx, rows) {
        const contentWidth = PRINTER_WIDTH - PADDING_X * 2;
        let height = PADDING_X;

        rows.forEach((row) => {
            if (row.type === 'gap') {
                height += row.height;
                return;
            }

            if (row.type === 'rule') {
                height += 16;
                return;
            }

            const size = row.size || DEFAULT_FONT_SIZE;
            setFont(ctx, size, Boolean(row.bold));

            if (row.type === 'pair') {
                const leftLines = wrapText(ctx, row.left, contentWidth * 0.62);
                height += Math.max(leftLines.length, 1) * (size + 8);
                return;
            }

            const lines = wrapText(ctx, row.text, contentWidth);
            height += lines.length * (size + 8);
        });

        return height + PADDING_X;
    }

    function drawReceiptRows(ctx, rows) {
        const contentWidth = PRINTER_WIDTH - PADDING_X * 2;
        let y = PADDING_X + DEFAULT_FONT_SIZE;

        rows.forEach((row) => {
            if (row.type === 'gap') {
                y += row.height;
                return;
            }

            if (row.type === 'rule') {
                ctx.fillRect(PADDING_X, y - 8, contentWidth, 2);
                y += 16;
                return;
            }

            const size = row.size || DEFAULT_FONT_SIZE;
            setFont(ctx, size, Boolean(row.bold));

            if (row.type === 'pair') {
                const leftLines = wrapText(ctx, row.left, contentWidth * 0.62);
                leftLines.forEach((line, index) => {
                    ctx.textAlign = 'left';
                    ctx.textBaseline = 'alphabetic';
                    ctx.fillText(line, PADDING_X, y);
                    if (index === leftLines.length - 1) {
                        ctx.textAlign = 'right';
                        ctx.fillText(row.right, PRINTER_WIDTH - PADDING_X, y);
                    }
                    y += size + 8;
                });
                return;
            }

            const lines = wrapText(ctx, row.text, contentWidth);
            lines.forEach((line) => {
                ctx.textAlign = row.align === 'center' ? 'center' : 'left';
                ctx.textBaseline = 'alphabetic';
                const x = row.align === 'center' ? PRINTER_WIDTH / 2 : PADDING_X;
                ctx.fillText(line, x, y);
                y += size + 8;
            });
        });
    }

    async function renderReceiptCanvas(receipt) {
        await ensureMyanmarFont();

        const measureCanvas = document.createElement('canvas');
        measureCanvas.width = PRINTER_WIDTH;
        measureCanvas.height = 10;
        const measureCtx = measureCanvas.getContext('2d');

        const rows = buildReceiptLayout(receipt);
        const height = measureReceiptHeight(measureCtx, rows);

        const canvas = document.createElement('canvas');
        canvas.width = PRINTER_WIDTH;
        canvas.height = height;

        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#000000';
        drawReceiptRows(ctx, rows);

        return canvas;
    }

    function appendBytes(target, bytes) {
        target.push(...bytes);
    }

    function canvasToEscPosRaster(canvas) {
        const ctx = canvas.getContext('2d');
        const { width, height } = canvas;
        const imageData = ctx.getImageData(0, 0, width, height);
        const bytesPerRow = Math.ceil(width / 8);
        const output = [];

        appendBytes(output, [0x1b, 0x40]);

        for (let bandStart = 0; bandStart < height; bandStart += RASTER_BAND_HEIGHT) {
            const bandHeight = Math.min(RASTER_BAND_HEIGHT, height - bandStart);
            const bandBytes = [];

            for (let row = 0; row < bandHeight; row += 1) {
                const y = bandStart + row;
                const rowBytes = new Uint8Array(bytesPerRow);

                for (let x = 0; x < width; x += 1) {
                    const pixelIndex = (y * width + x) * 4;
                    const luminance = (
                        imageData.data[pixelIndex] * 0.299
                        + imageData.data[pixelIndex + 1] * 0.587
                        + imageData.data[pixelIndex + 2] * 0.114
                    );

                    if (luminance < 168) {
                        const byteIndex = Math.floor(x / 8);
                        rowBytes[byteIndex] |= 0x80 >> (x % 8);
                    }
                }

                bandBytes.push(...rowBytes);
            }

            const xL = bytesPerRow & 0xff;
            const xH = (bytesPerRow >> 8) & 0xff;
            const yL = bandHeight & 0xff;
            const yH = (bandHeight >> 8) & 0xff;

            appendBytes(output, [0x1d, 0x76, 0x30, 0x00, xL, xH, yL, yH]);
            appendBytes(output, bandBytes);
        }

        appendBytes(output, [0x1d, 0x56, 0x00]);
        return new Uint8Array(output);
    }

    async function buildEscPosReceipt(receipt) {
        const canvas = await renderReceiptCanvas(receipt);
        return canvasToEscPosRaster(canvas);
    }

    async function findWritableCharacteristic(server) {
        for (const profile of PRINTER_PROFILES) {
            try {
                const service = await server.getPrimaryService(profile.service);
                const characteristic = await service.getCharacteristic(profile.write);
                return characteristic;
            } catch (err) {
                // Try the next known printer profile.
            }
        }

        const services = await server.getPrimaryServices();
        for (const service of services) {
            const characteristics = await service.getCharacteristics();
            for (const characteristic of characteristics) {
                if (characteristic.properties.write || characteristic.properties.writeWithoutResponse) {
                    return characteristic;
                }
            }
        }

        throw new Error('No writable printer characteristic found. Check that the printer supports BLE printing.');
    }

    async function connectToDevice(device) {
        if (bluetoothDevice && bluetoothDevice !== device) {
            bluetoothDevice.removeEventListener('gattserverdisconnected', onGattDisconnected);
        }

        bluetoothDevice = device;
        bluetoothDevice.removeEventListener('gattserverdisconnected', onGattDisconnected);
        bluetoothDevice.addEventListener('gattserverdisconnected', onGattDisconnected);

        const server = await bluetoothDevice.gatt.connect();
        writeCharacteristic = await findWritableCharacteristic(server);
        localStorage.setItem(PRINTER_ID_KEY, device.id);
        return bluetoothDevice.name || 'Bluetooth printer';
    }

    function scheduleAutoReconnect() {
        if (reconnectTimer || reconnectInFlight) {
            return;
        }

        if (!localStorage.getItem(PRINTER_ID_KEY)) {
            return;
        }

        reconnectTimer = setTimeout(async () => {
            reconnectTimer = null;
            reconnectInFlight = true;
            try {
                await tryReconnectStoredPrinter(lastStatusEl);
            } finally {
                reconnectInFlight = false;
            }
        }, 800);
    }

    function onGattDisconnected() {
        writeCharacteristic = null;
        if (lastStatusEl) {
            updatePrinterStatus(
                lastStatusEl,
                'Printer disconnected. Reconnecting...',
                'text-xs text-amber-600'
            );
        }
        scheduleAutoReconnect();
    }

    function updatePrinterStatus(statusEl, text, className) {
        if (!statusEl) return;
        statusEl.innerText = text;
        statusEl.className = className;
    }

    async function tryReconnectStoredPrinter(statusEl) {
        if (statusEl) {
            lastStatusEl = statusEl;
        }

        if (!navigator.bluetooth || !navigator.bluetooth.getDevices) {
            return false;
        }

        const savedId = localStorage.getItem(PRINTER_ID_KEY);
        if (!savedId) {
            return false;
        }

        if (writeCharacteristic && bluetoothDevice?.gatt?.connected && bluetoothDevice.id === savedId) {
            updatePrinterStatus(
                statusEl,
                `Connected: ${getPrinterName()}`,
                'text-xs text-emerald-600 font-semibold'
            );
            return true;
        }

        const devices = await navigator.bluetooth.getDevices();
        const device = devices.find((entry) => entry.id === savedId);
        if (!device) {
            return false;
        }

        try {
            updatePrinterStatus(statusEl, 'Reconnecting to printer...', 'text-xs text-blue-600');
            const printerName = await connectToDevice(device);
            updatePrinterStatus(
                statusEl,
                `Connected: ${printerName}`,
                'text-xs text-emerald-600 font-semibold'
            );
            return true;
        } catch (err) {
            writeCharacteristic = null;
            return false;
        }
    }

    async function tryReconnectStoredPrinterWithRetries(statusEl, maxAttempts = 3, delayMs = 600) {
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            const connected = await tryReconnectStoredPrinter(statusEl);
            if (connected) {
                return true;
            }

            if (attempt < maxAttempts) {
                await new Promise((resolve) => setTimeout(resolve, delayMs * attempt));
            }
        }

        if (statusEl && localStorage.getItem(PRINTER_ID_KEY)) {
            updatePrinterStatus(
                statusEl,
                'Printer not connected. Tap Connect Bluetooth Printer.',
                'text-xs text-amber-600'
            );
        }

        return false;
    }

    async function connectBluetoothPrinter() {
        if (!navigator.bluetooth) {
            throw new Error('Web Bluetooth is not supported. Use Chrome on Android over HTTPS.');
        }

        const optionalServices = PRINTER_PROFILES.map((profile) => profile.service);
        const device = await navigator.bluetooth.requestDevice({
            acceptAllDevices: true,
            optionalServices,
        });

        return connectToDevice(device);
    }

    async function ensurePrinterConnected(statusEl) {
        if (writeCharacteristic && bluetoothDevice?.gatt?.connected) {
            return getPrinterName();
        }

        writeCharacteristic = null;

        const reconnected = await tryReconnectStoredPrinter(statusEl);
        if (reconnected) {
            return getPrinterName();
        }

        return connectPrinterWithStatus(statusEl);
    }

    async function sendEscPosData(data) {
        if (!writeCharacteristic) {
            throw new Error('Printer not connected. Tap "Connect Printer" first.');
        }

        const chunkSize = 180;
        for (let i = 0; i < data.length; i += chunkSize) {
            const chunk = data.slice(i, i + chunkSize);
            if (writeCharacteristic.properties.writeWithoutResponse) {
                await writeCharacteristic.writeValueWithoutResponse(chunk);
            } else {
                await writeCharacteristic.writeValue(chunk);
            }
        }
    }

    function setCurrentReceipt(receipt) {
        currentReceipt = receipt;
    }

    function isPrinterConnected() {
        return Boolean(writeCharacteristic);
    }

    function getPrinterName() {
        return bluetoothDevice ? (bluetoothDevice.name || 'Bluetooth printer') : null;
    }

    async function connectPrinterWithStatus(statusEl) {
        const reconnected = await tryReconnectStoredPrinter(statusEl);
        if (reconnected) {
            return getPrinterName();
        }

        updatePrinterStatus(statusEl, 'Searching for printer...', 'text-xs text-blue-600');

        const printerName = await connectBluetoothPrinter();

        updatePrinterStatus(
            statusEl,
            `Connected: ${printerName}`,
            'text-xs text-emerald-600 font-semibold'
        );

        return printerName;
    }

    async function printCurrentReceipt(statusEl) {
        if (!currentReceipt) {
            throw new Error('No receipt loaded. Open a receipt first.');
        }

        updatePrinterStatus(statusEl, 'Preparing receipt...', 'text-xs text-blue-600');

        await ensurePrinterConnected(statusEl);

        const data = await buildEscPosReceipt(currentReceipt);

        updatePrinterStatus(statusEl, 'Printing...', 'text-xs text-blue-600');
        await sendEscPosData(data);

        updatePrinterStatus(
            statusEl,
            `Printed on ${getPrinterName()}`,
            'text-xs text-emerald-600 font-semibold'
        );
    }

    global.BluetoothPrinter = {
        setCurrentReceipt,
        buildEscPosReceipt,
        connectBluetoothPrinter,
        connectPrinterWithStatus,
        tryReconnectStoredPrinter,
        tryReconnectStoredPrinterWithRetries,
        printCurrentReceipt,
        isPrinterConnected,
        getPrinterName,
    };
}(window));
