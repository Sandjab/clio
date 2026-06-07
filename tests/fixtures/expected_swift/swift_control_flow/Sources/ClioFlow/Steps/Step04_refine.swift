import Foundation

// refine — exact step (fill in the body)
struct Step04_refine_In: Codable, Sendable {
    var x: String
}

struct Step04_refine_Out: Codable, Sendable {
    var p: Progress
}

func step_refine(_ input: Step04_refine_In) async throws -> Step04_refine_Out {
    fatalError("fill me in: refine")
}
