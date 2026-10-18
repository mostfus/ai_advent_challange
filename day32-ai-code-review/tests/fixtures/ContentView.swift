import SwiftUI

/// The first screen: a list of notes and a button that adds one.
@MainActor
struct ContentView: View {
    @StateObject private var store = NoteStore()
    let title = "Notes {draft}"   // a brace in a string is not a brace

    var body: some View {
        NavigationStack {
            List(store.notes) { note in
                Text(note.text)
            }
            .navigationTitle(title)
        }
    }

    /* Adds an empty note.
       { not a brace either } */
    private func addNote() {
        store.add(Note(text: ""))
    }
}

protocol NoteStoring {
    func add(_ note: Note)
    var notes: [Note] { get }
}

final class NoteStore: ObservableObject, NoteStoring {
    @Published private(set) var notes: [Note] = []

    func add(_ note: Note) {
        notes.append(note)
    }
}

extension Note {
    static let empty = Note(text: #"raw "quoted" {text}"#)
}

#Preview {
    ContentView()
}
