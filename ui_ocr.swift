import Foundation
import Vision
import ImageIO

// Persistent process: one cropped image path in, one JSON result out.
while let path = readLine() {
    autoreleasepool {
        do {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.usesLanguageCorrection = false
            let start = DispatchTime.now().uptimeNanoseconds
            try VNImageRequestHandler(url: URL(fileURLWithPath:path)).perform([request])
            let items = (request.results ?? []).compactMap { result -> [String:Any]? in
                guard let candidate = result.topCandidates(1).first else { return nil }
                let b = result.boundingBox
                return ["text":candidate.string,"confidence":candidate.confidence,
                        "box":[b.origin.x,b.origin.y,b.size.width,b.size.height]]
            }
            let value:[String:Any] = ["items":items,"ocr_ms":Double(DispatchTime.now().uptimeNanoseconds-start)/1e6]
            let data = try JSONSerialization.data(withJSONObject:value)
            print(String(data:data,encoding:.utf8)!)
        } catch {
            print("{\"error\":\"ocr_failed\"}")
        }
        fflush(stdout)
    }
}
