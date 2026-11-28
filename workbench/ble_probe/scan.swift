// Passive BLE advertisement scan via CoreBluetooth: JSON to <out.json>, a table to stdout.
// Usage: BLEScan [seconds, default 15] [out.json, default ble-scan.json]
import CoreBluetooth
import Foundation

let seconds = Double(CommandLine.arguments.dropFirst().first ?? "15") ?? 15
let outPath = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "ble-scan.json"

let companies: [UInt16: String] = [
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
    0x0087: "Garmin", 0x0157: "Huami", 0x038F: "Xiaomi", 0x0059: "Nordic",
    0x000F: "Broadcom", 0x0171: "Amazon", 0x02E5: "Espressif", 0x0499: "Ruuvi",
    0x0310: "SGL Italia", 0x0131: "Cypress", 0x01DA: "Logitech", 0x0822: "adidas",
    0x0590: "Tile", 0x067C: "Tile", 0x0002: "Intel", 0x000A: "Qualcomm",
]
let appleTypes: [UInt8: String] = [
    0x02: "iBeacon", 0x05: "AirDrop", 0x07: "ProximityPairing", 0x09: "AirPlayTarget",
    0x0C: "Handoff", 0x0D: "WiFiSettings", 0x0E: "InstantHotspot", 0x0F: "NearbyAction",
    0x10: "NearbyInfo", 0x12: "FindMy", 0x16: "AirPrint",
]

struct Seen: Codable {
    var id: String
    var name: String?
    var rssiMin: Int, rssiMax: Int, rssiLast: Int
    var count: Int
    var connectable: Bool?
    var txPower: Int?
    var manufacturer: String?
    var manufacturerData: String?
    var appleType: String?
    var services: [String]
    var firstSeen: Date, lastSeen: Date
}

final class Scanner: NSObject, CBCentralManagerDelegate {
    var central: CBCentralManager!
    var seen: [String: Seen] = [:]
    var done = false

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:
            FileHandle.standardError.write("scanning for \(Int(seconds))s...\n".data(using: .utf8)!)
            c.scanForPeripherals(withServices: nil,
                                 options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
        case .unauthorized:
            let msg = "Bluetooth permission denied for this process (TCC)\n"
            FileHandle.standardError.write(msg.data(using: .utf8)!)
            exit(2)
        case .poweredOff:
            FileHandle.standardError.write("Bluetooth is off\n".data(using: .utf8)!)
            exit(3)
        case .unsupported:
            FileHandle.standardError.write("BLE unsupported\n".data(using: .utf8)!)
            exit(4)
        default:
            break  // .unknown / .resetting settle into one of the above
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData ad: [String: Any], rssi RSSI: NSNumber) {
        let rssi = RSSI.intValue
        guard rssi != 127 else { return }  // 127 means "RSSI unavailable"
        let id = p.identifier.uuidString
        let now = Date()
        let name = (ad[CBAdvertisementDataLocalNameKey] as? String) ?? p.name
        var mfr: String?, mfrHex: String?, appleType: String?
        if let d = ad[CBAdvertisementDataManufacturerDataKey] as? Data, d.count >= 2 {
            let cid = UInt16(d[d.startIndex]) | UInt16(d[d.startIndex + 1]) << 8
            mfr = companies[cid] ?? String(format: "0x%04X", cid)
            mfrHex = d.map { String(format: "%02x", $0) }.joined()
            if cid == 0x004C, d.count >= 3 {
                let t = d[d.startIndex + 2]
                appleType = appleTypes[t] ?? String(format: "0x%02X", t)
            }
        }
        let svcs = ((ad[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []).map(\.uuidString)
            + ((ad[CBAdvertisementDataServiceDataKey] as? [CBUUID: Data]) ?? [:]).keys
                .map { "data:" + $0.uuidString }
        var s = seen[id] ?? Seen(id: id, name: nil, rssiMin: rssi, rssiMax: rssi, rssiLast: rssi,
                                 count: 0, connectable: nil, txPower: nil, manufacturer: nil,
                                 manufacturerData: nil, appleType: nil, services: [],
                                 firstSeen: now, lastSeen: now)
        s.count += 1
        s.rssiMin = min(s.rssiMin, rssi); s.rssiMax = max(s.rssiMax, rssi); s.rssiLast = rssi
        s.lastSeen = now
        if let name { s.name = name }
        if let c = ad[CBAdvertisementDataIsConnectable] as? NSNumber { s.connectable = c.boolValue }
        if let t = ad[CBAdvertisementDataTxPowerLevelKey] as? NSNumber { s.txPower = t.intValue }
        if let mfr { s.manufacturer = mfr; s.manufacturerData = mfrHex }
        if let appleType { s.appleType = appleType }
        s.services = Array(Set(s.services + svcs)).sorted()
        seen[id] = s
    }
}

let scanner = Scanner()
let deadline = Date().addingTimeInterval(seconds + 90)  // headroom for answering the TCC prompt
var scanStart: Date?
while Date() < deadline {
    RunLoop.main.run(until: Date().addingTimeInterval(0.25))
    if scanner.central.isScanning, scanStart == nil { scanStart = Date() }
    if let st = scanStart, Date().timeIntervalSince(st) >= seconds { break }
}
scanner.central.stopScan()
guard scanStart != nil else {
    let msg = "scan never started (state=\(scanner.central.state.rawValue))\n"
    FileHandle.standardError.write(msg.data(using: .utf8)!)
    exit(5)
}

let rows = scanner.seen.values.sorted { $0.rssiMax > $1.rssiMax }
let enc = JSONEncoder()
enc.outputFormatting = [.prettyPrinted, .sortedKeys]
enc.dateEncodingStrategy = .iso8601
try enc.encode(rows).write(to: URL(fileURLWithPath: outPath))

func pad(_ s: String, _ n: Int) -> String {
    s.count >= n ? String(s.prefix(n)) : s + String(repeating: " ", count: n - s.count)
}
print(pad("RSSI", 5), pad("NAME", 26), pad("MFR", 11), pad("KIND", 17), pad("CONN", 5), "SERVICES")
for r in rows {
    print(pad(String(r.rssiMax), 5), pad(r.name ?? "-", 26), pad(r.manufacturer ?? "-", 11),
          pad(r.appleType ?? "-", 17), pad(r.connectable.map { $0 ? "y" : "n" } ?? "?", 5),
          r.services.joined(separator: ","))
}
print("\n\(rows.count) devices; JSON: \(outPath)")
