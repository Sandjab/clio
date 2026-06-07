import Foundation

// detect_all — exact step (fill in the body)
struct Step01_detect_all_In: Codable, Sendable {
    var x: String
}

struct Step01_detect_all_Out: Codable, Sendable {
    var assessments: [RiskAssessment]
}

func step_detect_all(_ input: Step01_detect_all_In) async throws -> Step01_detect_all_Out {
    fatalError("fill me in: detect_all")
}
