import Foundation

// assess — exact step (fill in the body)
struct Step01_assess_In: Codable, Sendable {
    var x: String
}

struct Step01_assess_Out: Codable, Sendable {
    var r: Risk
}

func step_assess(_ input: Step01_assess_In) async throws -> Step01_assess_Out {
    fatalError("fill me in: assess")
}
