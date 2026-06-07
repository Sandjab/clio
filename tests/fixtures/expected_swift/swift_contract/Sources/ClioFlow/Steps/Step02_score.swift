import Foundation

// score — exact step (fill in the body)
struct Step02_score_In: Codable, Sendable {
    var clients: [String]
}

struct Step02_score_Out: Codable, Sendable {
    var risk: CustomerRisk
}

func step_score(_ input: Step02_score_In) async throws -> Step02_score_Out {
    fatalError("fill me in: score")
}
