// Capture JPEG frames at a fixed rate and log per-frame mean color, with wall-clock timestamps.
// Usage: LightMeter <outdir> <seconds> <fps> [lock]
//   lock: let auto-exposure settle ~2 s, then lock exposure/white balance if the camera allows it.
// Must run from the .app bundle that build.sh makes; see README.md.
import AVFoundation
import CoreImage
import Foundation

let args = CommandLine.arguments
guard args.count >= 4 else {
    let usage = "usage: LightMeter <outdir> <seconds> <fps> [lock]\n"
    FileHandle.standardError.write(usage.data(using: .utf8)!)
    exit(64)
}
let outDir = URL(fileURLWithPath: args[1], isDirectory: true)
let seconds = Double(args[2]) ?? 5
let fps = Double(args[3]) ?? 2
let wantLock = args.count > 4 && args[4] == "lock"
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
var log = ""
func note(_ s: String) {
    let line = String(format: "%.3f ", Date().timeIntervalSince1970) + s
    log += line + "\n"
    FileHandle.standardError.write((line + "\n").data(using: .utf8)!)
}

let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!

/// Mean color of `rect` in `img`, as sRGB RGBA bytes. region.swift computes it the same way.
func meanColor(_ ctx: CIContext, _ img: CIImage, _ rect: CGRect) -> [UInt8] {
    var px = [UInt8](repeating: 0, count: 4)
    let params: [String: Any] = [kCIInputImageKey: img, kCIInputExtentKey: CIVector(cgRect: rect)]
    if let avg = CIFilter(name: "CIAreaAverage", parameters: params)?.outputImage {
        ctx.render(avg, toBitmap: &px, rowBytes: 4, bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
                   format: .RGBA8, colorSpace: sRGB)
    }
    return px
}

final class Grabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    let ctx = CIContext()
    var lastSave = 0.0
    var count = 0
    var csv = "epoch,file,r,g,b,luma\n"
    var recording = false

    func captureOutput(_ o: AVCaptureOutput, didOutput sb: CMSampleBuffer,
                       from c: AVCaptureConnection) {
        guard recording, let pb = CMSampleBufferGetImageBuffer(sb) else { return }
        let now = Date().timeIntervalSince1970
        guard now - lastSave >= 1.0 / fps else { return }
        lastSave = now
        let name = String(format: "f%04d.jpg", count)
        // Measure the JPEG as saved, so frames.csv and region.swift read the same pixels.
        guard let data = ctx.jpegRepresentation(of: CIImage(cvPixelBuffer: pb), colorSpace: sRGB,
                                                options: [:]),
              let saved = CIImage(data: data) else { return }
        try? data.write(to: outDir.appendingPathComponent(name))
        let px = meanColor(ctx, saved, saved.extent)
        let luma = 0.2126 * Double(px[0]) + 0.7152 * Double(px[1]) + 0.0722 * Double(px[2])
        csv += String(format: "%.3f,%@,%d,%d,%d,%.1f\n", now, name, px[0], px[1], px[2], luma)
        count += 1
    }
}

var authorized = AVCaptureDevice.authorizationStatus(for: .video) == .authorized
if !authorized {
    let status = AVCaptureDevice.authorizationStatus(for: .video).rawValue
    note("requesting camera access (status=\(status))")
    let sem = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .video) { ok in authorized = ok; sem.signal() }
    sem.wait()
}
guard authorized else { note("camera access denied"); exit(2) }

let session = AVCaptureSession()
guard let dev = AVCaptureDevice.default(for: .video) else { note("no camera"); exit(3) }
note("camera: \(dev.localizedName)")
let input = try AVCaptureDeviceInput(device: dev)
session.addInput(input)
let output = AVCaptureVideoDataOutput()
output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
let grabber = Grabber()
let q = DispatchQueue(label: "cam")
output.setSampleBufferDelegate(grabber, queue: q)
session.addOutput(output)
session.startRunning()
let canLockExposure = dev.isExposureModeSupported(.locked)
let canLockWhiteBalance = dev.isWhiteBalanceModeSupported(.locked)
note("session running; exposure lock supported=\(canLockExposure) "
     + "wb lock supported=\(canLockWhiteBalance)")

func pump(_ s: Double) { RunLoop.main.run(until: Date().addingTimeInterval(s)) }
pump(2.0)  // auto-exposure settle
if wantLock {
    do {
        try dev.lockForConfiguration()
        if canLockExposure { dev.exposureMode = .locked; note("exposure locked") }
        if canLockWhiteBalance { dev.whiteBalanceMode = .locked; note("white balance locked") }
        dev.unlockForConfiguration()
    } catch { note("lock failed: \(error)") }
}
q.sync { grabber.recording = true }
note("recording start")
pump(seconds)
q.sync { grabber.recording = false }
note("recording stop; frames=\(grabber.count)")
session.stopRunning()
try? grabber.csv.write(to: outDir.appendingPathComponent("frames.csv"), atomically: true,
                       encoding: .utf8)
try? log.write(to: outDir.appendingPathComponent("cam.log"), atomically: true, encoding: .utf8)
