import Foundation

// archive — exact step (fill in the body)
struct Step02_archive_In: Codable, Sendable {
    var x: String
}

struct Step02_archive_Out: Codable, Sendable {
    var archived: Bool
}

func step_archive(_ input: Step02_archive_In) async throws -> Step02_archive_Out {
    fatalError("fill me in: archive")
}
