import io

p = "README.md"
s = io.open(p, encoding="utf-8").read()


def sub(old, new):
    global s
    assert old in s, old[:80]
    s = s.replace(old, new, 1)


sub("""**The window opens blank**
The local service may still be starting. Wait a second and refresh. If it persists, run
`bacpro --no-browser` from a terminal and open `http://127.0.0.1:5001` manually to see the
error.""",
    """**The window opens blank**
The local service may still be starting. Wait a second and refresh. If it persists, run
`bacpro --no-browser` from a terminal and open `http://127.0.0.1:5001` manually to see the
error, or read the log file listed in section 1.

**The app closes as soon as it opens**
On a machine with no Edge or Chrome, bacpro falls back to the default browser and then
relies on a page heartbeat; if the browser never loads the page it exits after
`--idle-timeout` seconds. Run with `--keep-alive` to pin it open.

**Modbus: every unit looks offline**
Wrong unit ID, wrong parity/baud on RTU, or a gateway that needs a small delay between
requests. Try a single read first (holding register 0, quantity 1) and watch the error
message: a timeout means no answer at all, an exception means the slave is there but does
not have that address.

**Modbus: \"address already in use\" starting the simulator**
Port 5020 is taken (often by a second bacpro instance or another Modbus tool). Change the
listen port in the simulator card.

**BBMD: registration refused**
The BBMD answered with a result code \u2014 read it in the toast and the Command log. The usual
cause is a BBMD that only accepts registrations from configured addresses, or a firewall
dropping UDP 47808 to that host. `code 2` (*register foreign device denied*) means the BBMD
policy said no; nothing on the client side can override that.""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")
