// Detect people in a directory of JPEGs using Apple's Vision framework.
// Prints "<filename>\t<count>\t<maxConfidence>" per frame, sorted by filename.
// ponytail: Vision ships with macOS — no model download, no torch, no cv2.

import Foundation
import Vision
import CoreImage

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: PersonDetect <frames-dir>\n".data(using: .utf8)!)
    exit(2)
}
let dir = URL(fileURLWithPath: args[1])
let files = (try! FileManager.default.contentsOfDirectory(atPath: dir.path))
    .filter { $0.hasSuffix(".jpg") }.sorted()

let handler = VNSequenceRequestHandler()
for name in files {
    let url = dir.appendingPathComponent(name)
    guard let img = CIImage(contentsOf: url) else { print("\(name)\t0\t0.0"); continue }
    let req = VNDetectHumanRectanglesRequest()
    req.upperBodyOnly = false
    var count = 0
    var best: Float = 0
    do {
        try handler.perform([req], on: img)
        for obs in (req.results ?? []) where obs.confidence >= 0.3 {
            count += 1
            best = max(best, obs.confidence)
        }
    } catch { }
    print("\(name)\t\(count)\t\(best)")
}
