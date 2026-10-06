import AppKit

/// Menu-bar stop for the day plan. Writes state/stop.request, which the bot
/// polls, including when it is a Windows process inside the Wizard101 bottle.
final class Stopper: NSObject, NSApplicationDelegate {
    let root: String
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    let stateItem = NSMenuItem(title: "Looking for the bot…", action: nil, keyEquivalent: "")
    var timer: Timer?

    init(root: String) {
        self.root = root
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        menu.addItem(.separator())

        let stop = NSMenuItem(title: "Stop and unhook", action: #selector(stopBot), keyEquivalent: "q")
        stop.keyEquivalentModifierMask = [.command, .shift]
        stop.target = self
        menu.addItem(stop)

        let pause = NSMenuItem(title: "Pause or resume", action: #selector(pauseBot), keyEquivalent: "p")
        pause.keyEquivalentModifierMask = [.command, .shift]
        pause.target = self
        menu.addItem(pause)

        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Close this menu", action: #selector(NSApplication.terminate(_:)), keyEquivalent: ""))
        item.menu = menu

        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
        NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            let mods = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
            let key = event.charactersIgnoringModifiers?.lowercased()
            if mods.contains(.control), mods.contains(.shift), key == "q" {
                self?.write("stop.request", title: "Wiz stopping")
            }
        }
    }

    @objc func stopBot() {
        write("stop.request", title: "Wiz stopping")
    }

    @objc func pauseBot() {
        write("pause.request", title: "Wiz pause")
    }

    func write(_ name: String, title: String) {
        let dir = (root as NSString).appendingPathComponent("state")
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        let path = (dir as NSString).appendingPathComponent(name)
        try? Date().description.write(toFile: path, atomically: true, encoding: .utf8)
        item.button?.title = title
        stateItem.title = "Asked the bot to \(name == "stop.request" ? "unhook" : "pause"). You can play once it stops."
    }

    func refresh() {
        let path = (root as NSString).appendingPathComponent("state/status.json")
        guard
            let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
            let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            stateItem.title = "Not running"
            item.button?.title = "Wiz off"
            return
        }
        let state = obj["state"] as? String ?? ""
        let task = obj["task"] as? String ?? ""
        let when = obj["time"] as? Double ?? 0
        let age = Date().timeIntervalSince1970 - when
        if state == "stopped" || age > 20 {
            stateItem.title = "Not running"
            item.button?.title = "Wiz off"
            return
        }
        let label = task.isEmpty ? state : "\(state): \(task)"
        stateItem.title = "\(label) (\(Int(age))s)"
        item.button?.title = state == "paused" ? "Wiz paused" : "Wiz on"
    }
}

let root = CommandLine.arguments.dropFirst().first ?? FileManager.default.currentDirectoryPath
let app = NSApplication.shared
let stopper = Stopper(root: root)
app.delegate = stopper
app.setActivationPolicy(.accessory)
app.run()
