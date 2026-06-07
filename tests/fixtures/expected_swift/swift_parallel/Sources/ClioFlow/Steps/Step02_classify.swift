import Foundation

// classify — exact step (fill in the body)
struct Step02_classify_In: Codable, Sendable {
    var item: String
}

struct Step02_classify_Out: Codable, Sendable {
    var label: String
}

func step_classify(_ input: Step02_classify_In) async throws -> Step02_classify_Out {
    fatalError("fill me in: classify")
}
