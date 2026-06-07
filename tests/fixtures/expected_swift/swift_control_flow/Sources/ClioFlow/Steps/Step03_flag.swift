import Foundation

// flag — exact step (fill in the body)
struct Step03_flag_In: Codable, Sendable {
    var x: String
}

struct Step03_flag_Out: Codable, Sendable {
    var flagged: Bool
}

func step_flag(_ input: Step03_flag_In) async throws -> Step03_flag_Out {
    fatalError("fill me in: flag")
}
