/**
 * Bluetooth thermal printer support for Android Chrome (Web Bluetooth API).
 * Formats receipt JSON as ESC/POS and sends to paired BLE printers.
 */
(function (global) {
    const ESC = '\x1B';
    const GS = '\x1D';
    const LINE_WIDTH = 32;

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

    let bluetoothDevice = null;
    let writeCharacteristic = null;
    let currentReceipt = null;

    function padLine(left, right) {
        const leftText = String(left);
        const rightText = String(right);
        const spaces = Math.max(1, LINE_WIDTH - leftText.length - rightText.length);
        return leftText + ' '.repeat(spaces) + rightText + '\n';
    }

    function center(text) {
        const value = String(text);
        const padding = Math.max(0, Math.floor((LINE_WIDTH - value.length) / 2));
        return ' '.repeat(padding) + value + '\n';
    }

    function dashedLine() {
        return '-'.repeat(LINE_WIDTH) + '\n';
    }

    function formatMoney(amount) {
        return `${Number(amount).toFixed(0)} Ks`;
    }

    function buildEscPosReceipt(receipt) {
        let out = '';
        out += ESC + '@';
        out += ESC + 'a' + '\x01';

        const title = receipt.restaurant_name || '27 Cafe & Bar';
        out += center(title);

        if (receipt.voucher_id) {
            out += center(receipt.voucher_id);
        } else if (receipt.order_ids && receipt.order_ids.length) {
            out += center('TABLE SESSION INVOICE');
        }

        if (receipt.timestamp) {
            out += center(receipt.timestamp);
        }

        out += ESC + 'a' + '\x00';
        out += dashedLine();
        out += `Table: ${receipt.table_number}\n`;

        if (receipt.order_ids && receipt.order_ids.length) {
            out += `Orders: ${receipt.order_ids.map((id) => `#${id}`).join(', ')}\n`;
        }

        out += dashedLine();

        (receipt.items || []).forEach((item) => {
            const subtotal = item.subtotal != null
                ? item.subtotal
                : item.quantity * item.unit_price;
            out += padLine(`${item.name} x${item.quantity}`, formatMoney(subtotal));

            const mods = item.modifiers || [];
            mods.forEach((mod) => {
                const modName = typeof mod === 'string' ? mod : mod.name;
                if (modName) {
                    out += `  + ${modName}\n`;
                }
            });
        });


        out += ESC + 'E' + '\x01';
        out += padLine('GRAND TOTAL', formatMoney(receipt.subtotal));
        out += ESC + 'E' + '\x00';
        out += dashedLine();

        if (receipt.status) {
            out += `Status: ${receipt.status}\n`;
        }

        out += ESC + 'a' + '\x01';
        out += '\nThank you!\n';
        out += center(receipt.restaurant_name || '27 Cafe & Bar');
        out += '\n\n\n';
        out += GS + 'V' + '\x00';

        return new TextEncoder().encode(out);
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

    async function connectBluetoothPrinter() {
        if (!navigator.bluetooth) {
            throw new Error('Web Bluetooth is not supported. Use Chrome on Android over HTTPS.');
        }

        const optionalServices = PRINTER_PROFILES.map((profile) => profile.service);
        bluetoothDevice = await navigator.bluetooth.requestDevice({
            acceptAllDevices: true,
            optionalServices,
        });

        bluetoothDevice.addEventListener('gattserverdisconnected', () => {
            writeCharacteristic = null;
        });

        const server = await bluetoothDevice.gatt.connect();
        writeCharacteristic = await findWritableCharacteristic(server);
        return bluetoothDevice.name || 'Bluetooth printer';
    }

    async function sendEscPosData(data) {
        if (!writeCharacteristic) {
            throw new Error('Printer not connected. Tap "Connect Printer" first.');
        }

        const chunkSize = 100;
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
        if (statusEl) {
            statusEl.innerText = 'Searching for printer...';
            statusEl.className = 'text-xs text-blue-600';
        }

        const printerName = await connectBluetoothPrinter();

        if (statusEl) {
            statusEl.innerText = `Connected: ${printerName}`;
            statusEl.className = 'text-xs text-emerald-600 font-semibold';
        }

        return printerName;
    }

    async function printCurrentReceipt(statusEl) {
        if (!currentReceipt) {
            throw new Error('No receipt loaded. Open a receipt first.');
        }

        if (!writeCharacteristic) {
            await connectPrinterWithStatus(statusEl);
        }

        if (statusEl) {
            statusEl.innerText = 'Printing...';
            statusEl.className = 'text-xs text-blue-600';
        }

        const data = buildEscPosReceipt(currentReceipt);
        await sendEscPosData(data);

        if (statusEl) {
            statusEl.innerText = `Printed on ${getPrinterName()}`;
            statusEl.className = 'text-xs text-emerald-600 font-semibold';
        }
    }

    global.BluetoothPrinter = {
        setCurrentReceipt,
        buildEscPosReceipt,
        connectBluetoothPrinter,
        connectPrinterWithStatus,
        printCurrentReceipt,
        isPrinterConnected,
        getPrinterName,
    };
}(window));
