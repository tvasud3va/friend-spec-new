# Done means

The messaging app is done when a person using either client, in either language, can check every item below.

- [ ] **D-1** On first launch I log in with a name once; after that the client remembers me and never asks again. (rules: C-3, C-4, C-5, C-6)
- [ ] **D-2** I can send a text message to another user by name, and bad names or empty messages are refused with a clear error. (rules: C-11, C-12, P-7, P-8)
- [ ] **D-3** A message I send while offline is queued, and delivered automatically once I reconnect, even if the server once stopped answering. (rules: C-7, C-12, C-13, C-15, C-27)
- [ ] **D-4** Messages I receive are shown to me, in the order they were delivered, each exactly once. (rules: C-18, C-19, C-23)
- [ ] **D-5** Messages sent to someone who is offline reach them the next time they connect. (rules: P-18, P-13, P-23)
- [ ] **D-6** Nothing is lost if the client is killed: my name, my unsent messages and my inbox are still there on restart. (rules: C-1, C-2, C-14)
- [ ] **D-7** I can see when I am online and when I am not. (rules: C-8, C-9)
- [ ] **D-8** The assignment scenario works, with Alice and Bob on different clients: (1) Alice → Bob "Hi Bob, I have something important to tell you"; (2) Bob → Alice "What is it?"; (3) Alice goes offline and queues a message; (4) Bob goes offline and queues a message; (5) Alice reconnects and her queued message reaches the server; (6) Alice goes offline again; (7) Bob reconnects, receives Alice's message and delivers his own queued message. (rules: C-12, C-13, C-15, C-18, P-13, P-18, P-21, P-23)
- [ ] **D-9** The Python and TypeScript clients talk to each other through the same server. (rules: P-2, P-9, P-11)
- [ ] **D-10** The program can be scripted: commands on stdin, one JSON event per line on stdout, clear exit codes; typos and odd input get a clear error and never crash it. (rules: I-1, I-2, I-3, I-4, I-6, I-8, I-11, C-26)
