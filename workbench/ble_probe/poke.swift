// Connect, subscribe to a notify characteristic, then run a script of steps against a write
// characteristic, logging every notification. Writes go out with response, one at a time.
// Usage: BLEPoke [--service UUID] [--write UUID] [--notify UUID]
//                <peripheral-uuid> <out.txt> <step>...
//   step = hex bytes ("55 01 ff 06 00 a4") or "sleep:<seconds>"
//   defaults: service FFF0, write FFF3, notify FFF4 (common on cheap BLE light controllers)
import CoreBluetooth
import Foundation

var serviceID = CBUUID(string: "FFF0")
var writeID = CBUUID(string: "FFF3")
var notifyID = CBUUID(string: "FFF4")
var positional: [String] = []
var rest = Array(CommandLine.arguments.dropFirst())
while let arg = rest.first {
    rest.removeFirst()
    switch arg {
    case "--service", "--write", "--notify":
        guard let value = rest.first else { positional = []; rest = []; break }
        rest.removeFirst()
        let id = CBUUID(string: value)
        if arg == "--service" { serviceID = id } else if arg == "--write" { writeID = id } else {
            notifyID = id
        }
    default:
        positional.append(arg)
    }
}
let target: UUID = {
    guard positional.count >= 2, let id = UUID(uuidString: positional[0]) else {
        let usage = "usage: BLEPoke [--service UUID] [--write UUID] [--notify UUID] "
            + "<peripheral-uuid> <out.txt> <step>...\n"
        FileHandle.standardError.write(usage.data(using: .utf8)!)
        exit(64)
    }
    return id
}()
let outPath = positional[1]
let steps = Array(positional.dropFirst(2))
let t0 = Date()
var out = ""
func log(_ s: String) {
    let line = String(format: "[%6.2f] ", Date().timeIntervalSince(t0)) + s
    out += line + "\n"
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}
func hex(_ d: Data) -> String { d.map { String(format: "%02x", $0) }.joined(separator: " ") }
func parseHex(_ s: String) -> Data? {
    let clean = s.replacingOccurrences(of: " ", with: "")
    guard clean.count % 2 == 0 else { return nil }
    var d = Data()
    var i = clean.startIndex
    while i < clean.endIndex {
        let j = clean.index(i, offsetBy: 2)
        guard let byte = UInt8(clean[i..<j], radix: 16) else { return nil }
        d.append(byte)
        i = j
    }
    return d
}

final class Poker: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var p: CBPeripheral?
    var tx: CBCharacteristic?
    var ready = false
    var done = false
    var awaitingWrite = false
    override init() { super.init(); central = CBCentralManager(delegate: self, queue: nil) }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        guard c.state == .poweredOn else {
            if c.state == .unauthorized { log("unauthorized"); exit(2) }
            return
        }
        if let known = c.retrievePeripherals(withIdentifiers: [target]).first {
            connect(known)
        } else {
            log("scanning")
            c.scanForPeripherals(withServices: nil)
        }
    }
    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi: NSNumber) {
        if p.identifier == target { c.stopScan(); connect(p) }
    }
    func connect(_ per: CBPeripheral) {
        p = per
        per.delegate = self
        log("connecting")
        central.connect(per)
    }
    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        log("connected")
        p.discoverServices([serviceID])
    }
    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        log("connect failed: \(error?.localizedDescription ?? "?")")
        done = true
    }
    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral,
                        error: Error?) {
        log("disconnected: \(error?.localizedDescription ?? "clean")")
        done = true
    }
    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        for s in p.services ?? [] { p.discoverCharacteristics([writeID, notifyID], for: s) }
    }
    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        for ch in s.characteristics ?? [] {
            if ch.uuid == writeID { tx = ch }
            if ch.uuid == notifyID { p.setNotifyValue(true, for: ch) }
        }
    }
    func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor ch: CBCharacteristic,
                    error: Error?) {
        let state = ch.isNotifying ? "on" : "off"
        log("notify \(ch.uuid.uuidString) \(state) \(error?.localizedDescription ?? "")")
        ready = tx != nil
    }
    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        log("<- \(ch.uuid.uuidString): \(ch.value.map(hex) ?? "-")")
    }
    func peripheral(_ p: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
        log("   write ack \(error.map { "ERROR \($0.localizedDescription)" } ?? "ok")")
        awaitingWrite = false
    }
    func write(_ d: Data) {
        guard let p, let tx else { return }
        log("-> \(tx.uuid.uuidString): \(hex(d))")
        awaitingWrite = true
        p.writeValue(d, for: tx, type: .withResponse)
    }
}

func pump(_ secs: Double) { RunLoop.main.run(until: Date().addingTimeInterval(secs)) }

let poker = Poker()
let connectDeadline = Date().addingTimeInterval(90)
while !poker.ready, !poker.done, Date() < connectDeadline { pump(0.1) }
if !poker.ready {
    log("never became ready")
} else {
    for step in steps where !poker.done {
        if step.hasPrefix("sleep:") { pump(Double(step.dropFirst(6)) ?? 1); continue }
        guard let frame = parseHex(step) else { log("not hex, skipped: \(step)"); continue }
        poker.write(frame)
        let ackDeadline = Date().addingTimeInterval(5)
        while poker.awaitingWrite, Date() < ackDeadline { pump(0.05) }
        if poker.awaitingWrite { log("   no write ack within 5s") }
        pump(0.5)  // collect any reply notifications
    }
    pump(2)
}
if let p = poker.p { poker.central.cancelPeripheralConnection(p) }
pump(0.5)
try? out.write(toFile: outPath, atomically: true, encoding: .utf8)
