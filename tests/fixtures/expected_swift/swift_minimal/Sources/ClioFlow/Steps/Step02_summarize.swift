import Foundation

// summarize — exact step (fill in the body)
struct Step02_summarize_In: Codable, Sendable {
    var rows: [String]
}

struct Step02_summarize_Out: Codable, Sendable {
    var summary: String
}

func step_summarize(_ input: Step02_summarize_In) async throws -> Step02_summarize_Out {
    fatalError("fill me in: summarize")
}
