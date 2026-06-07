import Foundation
import ClioFlow

@main
struct CLI {
    static func main() async throws {
        var kwargs: [String: Any] = [:]
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--kwargs"),
           i + 1 < args.count,
           let data = args[i + 1].data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            kwargs = obj
        }
        let result = try await Flow.run(kwargs: kwargs)
        let out = try JSONSerialization.data(
            withJSONObject: result, options: [.sortedKeys]
        )
        print(String(data: out, encoding: .utf8) ?? "{}")
    }
}
