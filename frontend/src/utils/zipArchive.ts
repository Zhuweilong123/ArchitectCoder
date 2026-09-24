const encoder = new TextEncoder();

interface ZipFile {
  path: string;
  content: string;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date: Date): { date: number; time: number } {
  return {
    date: ((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
  };
}

function header(signature: number, length: number): { bytes: Uint8Array; view: DataView } {
  const bytes = new Uint8Array(length);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, signature, true);
  return { bytes, view };
}

/** Create a small UTF-8 ZIP archive using the ZIP store method. */
export function createZipBlob(files: ZipFile[]): Blob {
  const localParts: Uint8Array[] = [];
  const centralParts: Uint8Array[] = [];
  const now = dosDateTime(new Date());
  let localOffset = 0;

  files.forEach(({ path, content }) => {
    const name = encoder.encode(path.replace(/\\/g, '/'));
    const data = encoder.encode(content);
    const checksum = crc32(data);

    const local = header(0x04034b50, 30 + name.length);
    local.view.setUint16(4, 20, true);
    local.view.setUint16(6, 0x0800, true);
    local.view.setUint16(8, 0, true);
    local.view.setUint16(10, now.time, true);
    local.view.setUint16(12, now.date, true);
    local.view.setUint32(14, checksum, true);
    local.view.setUint32(18, data.length, true);
    local.view.setUint32(22, data.length, true);
    local.view.setUint16(26, name.length, true);
    local.bytes.set(name, 30);
    localParts.push(local.bytes, data);

    const central = header(0x02014b50, 46 + name.length);
    central.view.setUint16(4, 20, true);
    central.view.setUint16(6, 20, true);
    central.view.setUint16(8, 0x0800, true);
    central.view.setUint16(10, 0, true);
    central.view.setUint16(12, now.time, true);
    central.view.setUint16(14, now.date, true);
    central.view.setUint32(16, checksum, true);
    central.view.setUint32(20, data.length, true);
    central.view.setUint32(24, data.length, true);
    central.view.setUint16(28, name.length, true);
    central.view.setUint32(38, 0, true);
    central.view.setUint32(42, localOffset, true);
    central.bytes.set(name, 46);
    centralParts.push(central.bytes);
    localOffset += local.bytes.length + data.length;
  });

  const centralSize = centralParts.reduce((sum, part) => sum + part.length, 0);
  const end = header(0x06054b50, 22);
  end.view.setUint16(8, files.length, true);
  end.view.setUint16(10, files.length, true);
  end.view.setUint32(12, centralSize, true);
  end.view.setUint32(16, localOffset, true);

  return new Blob([...localParts, ...centralParts, end.bytes], { type: 'application/zip' });
}
