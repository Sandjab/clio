import Foundation

// load — exact step (fill in the body)
struct Step01_load_In: Codable, Sendable {
    var file: String
}

struct Step01_load_Out: Codable, Sendable {
    var clients: [String]
}

func step_load(_ input: Step01_load_In) async throws -> Step01_load_Out {
    fatalError("fill me in: load")
}
