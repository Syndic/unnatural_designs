// Mean RGB of a square region in each frame of a capture dir (top-left-origin pixel coords).
// Usage: swift region.swift <dir> <cx> <cy> <half-size>
import CoreImage
import Foundation

let a = CommandLine.arguments
guard a.count == 5 else {
    let usage = "usage: region.swift <dir> <cx> <cy> <half-size>\n"
    FileHandle.standardError.write(usage.data(using: .utf8)!)
    exit(64)
}
let dir = URL(fileURLWithPath: a[1])
let cx = Double(a[2])!, cy = Double(a[3])!, h = Double(a[4])!
let ctx = CIContext()
let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!
let csv = try String(contentsOf: dir.appendingPathComponent("frames.csv"), encoding: .utf8)
print("epoch,file,r,g,b")
for line in csv.split(separator: "\n").dropFirst() {
    let f = line.split(separator: ",")
    guard let img = CIImage(contentsOf: dir.appendingPathComponent(String(f[1]))) else { continue }
    let H = img.extent.height
    // Core Image's origin is bottom-left; the arguments are top-left.
    let rect = CGRect(x: cx - h, y: H - cy - h, width: 2 * h, height: 2 * h)
    // Outside the frame, CIAreaAverage averages in transparent pixels and reads dark.
    guard img.extent.contains(rect) else {
        let size = "\(Int(img.extent.width))x\(Int(img.extent.height))"
        FileHandle.standardError.write("skipped \(f[1]): region outside \(size) frame\n"
            .data(using: .utf8)!)
        continue
    }
    var px = [UInt8](repeating: 0, count: 4)
    let params: [String: Any] = [kCIInputImageKey: img, kCIInputExtentKey: CIVector(cgRect: rect)]
    let avg = CIFilter(name: "CIAreaAverage", parameters: params)!.outputImage!
    ctx.render(avg, toBitmap: &px, rowBytes: 4, bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
               format: .RGBA8, colorSpace: sRGB)
    print("\(f[0]),\(f[1]),\(px[0]),\(px[1]),\(px[2])")
}
