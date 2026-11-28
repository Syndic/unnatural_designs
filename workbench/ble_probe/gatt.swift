// Read-only GATT dump: connect to one peripheral, list services/characteristics/descriptors,
// read every readable value, briefly subscribe to notify chars. Never writes.
// Usage: BLEGatt <peripheral-uuid> <out.txt>
import CoreBluetooth
import Foundation

let target: UUID = {
    let args = CommandLine.arguments
    guard args.count == 3, let id = UUID(uuidString: args[1]) else {
        let usage = "usage: BLEGatt <peripheral-uuid> <out.txt>\n"
        FileHandle.standardError.write(usage.data(using: .utf8)!)
        exit(64)
    }
    return id
}()
let outPath = CommandLine.arguments[2]
var out = ""
func log(_ s: String) {
    out += s + "\n"
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}
func hex(_ d: Data?) -> String {
    d.map { $0.map { String(format: "%02x", $0) }.joined(separator: " ") } ?? "-"
}
func ascii(_ d: Data?) -> String {
    guard let d else { return "" }
    return String(d.map { (0x20...0x7e).contains($0) ? Character(UnicodeScalar($0)) : "." })
}
func props(_ p: CBCharacteristicProperties) -> String {
    var a: [String] = []
    if p.contains(.read) { a.append("read") }
    if p.contains(.write) { a.append("write") }
    if p.contains(.writeWithoutResponse) { a.append("writeNoResp") }
    if p.contains(.notify) { a.append("notify") }
    if p.contains(.indicate) { a.append("indicate") }
    if p.contains(.broadcast) { a.append("broadcast") }
    if p.contains(.authenticatedSignedWrites) { a.append("signedWrite") }
    return a.joined(separator: ",")
}

final class Dumper: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var p: CBPeripheral?
    var pending = 0
    var finished = false
    override init() { super.init(); central = CBCentralManager(delegate: self, queue: nil) }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        guard c.state == .poweredOn else {
            if c.state == .unauthorized { log("unauthorized"); exit(2) }
            return
        }
        if let known = c.retrievePeripherals(withIdentifiers: [target]).first {
            log("retrieved known peripheral; connecting")
            connect(known)
        } else {
            log("scanning for target")
            c.scanForPeripherals(withServices: nil)
        }
    }
    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi: NSNumber) {
        if p.identifier == target { c.stopScan(); connect(p) }
    }
    func connect(_ per: CBPeripheral) { p = per; per.delegate = self; central.connect(per) }
    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        log("connected: \(p.name ?? "?")")
        p.discoverServices(nil)
    }
    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        log("connect failed: \(error?.localizedDescription ?? "?")"); finished = true
    }
    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral,
                        error: Error?) {
        log("disconnected: \(error?.localizedDescription ?? "clean")"); finished = true
    }
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        if let error { log("service discovery error: \(error)") }
        for s in p.services ?? [] { pending += 1; p.discoverCharacteristics(nil, for: s) }
    }
    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        pending -= 1
        for ch in s.characteristics ?? [] {
            pending += 1; p.discoverDescriptors(for: ch)
            // A read reply and a notification arrive through the same callback, so a
            // characteristic is subscribed only once its read is answered.
            if ch.properties.contains(.read) {
                pending += 1
                readsOutstanding.insert(ObjectIdentifier(ch))
                p.readValue(for: ch)
            } else {
                subscribe(ch, on: p)
            }
        }
    }
    func peripheral(_ p: CBPeripheral, didDiscoverDescriptorsFor ch: CBCharacteristic,
                    error: Error?) {
        pending -= 1
        for d in ch.descriptors ?? [] { pending += 1; p.readValue(for: d) }
    }
    func peripheral(_ p: CBPeripheral, didUpdateValueFor d: CBDescriptor, error: Error?) {
        pending -= 1
    }
    var reads: [ObjectIdentifier: Data] = [:]
    var readsOutstanding: Set<ObjectIdentifier> = []
    var notifies: [(CBUUID, Data)] = []
    func subscribe(_ ch: CBCharacteristic, on p: CBPeripheral) {
        if ch.properties.contains(.notify) || ch.properties.contains(.indicate) {
            p.setNotifyValue(true, for: ch)
        }
    }
    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        if readsOutstanding.remove(ObjectIdentifier(ch)) != nil {
            pending -= 1
            reads[ObjectIdentifier(ch)] = ch.value ?? Data()
            if let error { log("read error \(ch.uuid): \(error.localizedDescription)") }
            subscribe(ch, on: p)
        } else if let v = ch.value {
            notifies.append((ch.uuid, v))
        }
    }

    func report() {
        guard let p else { return }
        log("\n=== GATT of \(p.name ?? "?") [\(p.identifier)] ===")
        for s in p.services ?? [] {
            log("service \(s.uuid.uuidString)\(s.isPrimary ? "" : " (secondary)") — \(s.uuid)")
            for ch in s.characteristics ?? [] {
                log("  char \(ch.uuid.uuidString) [\(props(ch.properties))] — \(ch.uuid)")
                if let v = reads[ObjectIdentifier(ch)] {
                    log("    value: \(hex(v))  |\(ascii(v))|")
                }
                for d in ch.descriptors ?? [] {
                    let v = d.value
                    let shown: String
                    if let s = v as? String { shown = "\"\(s)\"" }
                    else if let dd = v as? Data { shown = hex(dd) }
                    else if let n = v as? NSNumber { shown = n.stringValue }
                    else { shown = String(describing: v ?? "-") }
                    log("    desc \(d.uuid.uuidString): \(shown)")
                }
            }
        }
        log("\n=== notifications received during dump (\(notifies.count)) ===")
        for (u, v) in notifies { log("  \(u.uuidString): \(hex(v))  |\(ascii(v))|") }
    }
}

let dumper = Dumper()
let hardStop = Date().addingTimeInterval(60)
var settledAt: Date?
while Date() < hardStop, !dumper.finished {
    RunLoop.main.run(until: Date().addingTimeInterval(0.2))
    if dumper.p?.services != nil, dumper.pending == 0 {
        settledAt = settledAt ?? Date()
        if Date().timeIntervalSince(settledAt!) > 5 { break }  // linger 5s to catch notifications
    } else { settledAt = nil }
}
dumper.report()
if let p = dumper.p { dumper.central.cancelPeripheralConnection(p) }
RunLoop.main.run(until: Date().addingTimeInterval(0.5))
try? out.write(toFile: outPath, atomically: true, encoding: .utf8)
