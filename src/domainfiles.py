from PIL import Image, ImageFilter
from io import BytesIO
import time
import requests
import re
import random
import string
import freedns
import sys
import pytesseract
import os
import platform
import temp_mails


# -----------------------------
# Configuration
# -----------------------------
class Args:
    number = 5
    ip = "129.153.136.235"  # Always use this IP
    webhook = "https://discord.com/api/webhooks/1439083402937503908/D2lcCIfB-7qGk3DAMiEp5m6A8Kin_7n2eICOvo_yH_-uDYbVcO8QUhur7bPu9C-jVfvD"
    proxy = None
    use_tor = False
    silent = False  # logs enabled
    outfile = "created_domains.txt"
    type = "A"
    pages = "1-10"
    subdomains = "random"
    auto = True
    domain_type = None
    single_tld = None
    domains_file = "domainlist.txt"  # NEW: Load domains from this file


args = Args()
client = freedns.Client()

if platform.system() != "Linux":
    raise EnvironmentError("This script only supports Linux.")

script_dir = os.path.dirname(__file__)
filename = os.path.join(script_dir, "data", "tesseract-linux")
os.environ["TESSDATA_PREFIX"] = os.path.join(script_dir, "data")
pytesseract.pytesseract.tesseract_cmd = filename

domainlist = []
domainnames = []


# -----------------------------
# Logging
# -----------------------------
def log(msg):
    if not args.silent:
        print(msg)


# -----------------------------
# Page parsing (used only if no domains_file)
# -----------------------------
def getpagelist(arg):
    arg = arg.strip()
    if not arg:
        sys.exit(1)
    pagelist = []
    for item in arg.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            sp, ep = map(int, item.split("-"))
            if sp < 1 or sp > ep:
                sys.exit(1)
            pagelist.extend(range(sp, ep + 1))
        else:
            p = int(item)
            if p < 1:
                sys.exit(1)
            pagelist.append(p)
    return sorted(set(pagelist))


def getdomains(arg, headergen):
    global domainlist, domainnames
    for sp in getpagelist(arg):
        try:
            html = requests.get(
                f"https://freedns.afraid.org/domain/registry/?page={sp}&sort=2&q=",
                headers=headergen(),
            )
            if html.status_code != 200 or not html.text:
                log(
                    f"[!] Blocked or failed to fetch page {sp} (HTTP {html.status_code})"
                )
                continue
            html = html.text
            if args.domain_type == "private":
                pattern = r"<a href=\/subdomain\/edit\.php\?edit_domain_id=(\d+)>([\w.-]+)<\/a>(.+\..+)<td>private<\/td>"
            elif args.domain_type == "public":
                pattern = r"<a href=\/subdomain\/edit\.php\?edit_domain_id=(\d+)>([\w.-]+)<\/a>(.+\..+)<td>public<\/td>"
            else:
                pattern = r"<a href=\/subdomain\/edit\.php\?edit_domain_id=(\d+)>([\w.-]+)<\/a>(.+\..+)<td>(public|private)<\/td>"
            matches = re.findall(pattern, html)
            domainnames.extend([m[1] for m in matches])
            domainlist.extend([m[0] for m in matches])
        except Exception as e:
            log(f"[!] Error fetching page {sp}: {e}")


def find_domain_id(domain_name, headergen):
    return client.get_registry(query=domain_name)["domains"][0]["id"]


# -----------------------------
# NEW: Load domains from file
# -----------------------------
def load_domains_from_file(path):
    global domainlist, domainnames
    if not os.path.isfile(path):
        log(f"[!] Domains file not found: {path}")
        sys.exit(1)

    base_domains = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                base_domains.append(line)

    if not base_domains:
        log("[!] Domains file is empty or contains only comments.")
        sys.exit(1)

    log(f"[+] Loaded {len(base_domains)} base domains from {path}")

    # Create fake IDs (placeholders) — real ID will be resolved via name
    domainlist = [str(1000000 + i) for i in range(len(base_domains))]
    domainnames = base_domains

    # If single_tld is set, filter to only that domain
    if args.single_tld:
        if args.single_tld in domainnames:
            idx = domainnames.index(args.single_tld)
            domainlist = [domainlist[idx]]
            domainnames = [domainnames[idx]]
            log(f"[+] Using single TLD from file: {args.single_tld}")
        else:
            log(f"[!] single_tld '{args.single_tld}' not found in {path}")
            sys.exit(1)


# -----------------------------
# Captcha
# -----------------------------
def getcaptcha():
    return Image.open(BytesIO(client.get_captcha()))


def denoise(img):
    imgarr = img.load()
    newimg = Image.new("RGB", img.size)
    newimgarr = newimg.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = imgarr[x, y]
            newimgarr[x, y] = (
                (255, 255, 255) if (r, g, b) == (255, 255, 255) else (0, 0, 0)
            )
    return newimg


def solve(image):
    try:
        image = denoise(image)
        text = (
            pytesseract.image_to_string(
                image.filter(ImageFilter.GaussianBlur(1)).convert("1"),
                config="-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ --psm 13 -l freednsocr",
            )
            .strip()
            .upper()
        )
        if len(text) not in (4, 5):
            log("[!] Captcha does not match expected length, retrying...")
            return solve(getcaptcha())
        return text
    except Exception as e:
        log(f"[!] Captcha solving error: {e}")
        return solve(getcaptcha())


def generate_random_string(length):
    return "".join(random.choice(string.ascii_lowercase) for _ in range(length))


# -----------------------------
# Login & account creation
# -----------------------------
def login(headergen):
    while True:
        try:
            capcha = solve(getcaptcha()) if args.auto else input("Captcha: ")
            mail = temp_mails.Generator_email()
            email = mail.email
            username = generate_random_string(random.randint(8, 13))
            client.create_account(
                capcha,
                generate_random_string(13),
                generate_random_string(13),
                username,
                "alphabet11",
                email,
            )
            text = mail.wait_for_new_email(timeout=30)
            content = str(mail.get_mail_content(mail_id=text["id"]))
            match = re.search(r'\?([^">]+)"', content)
            if match:
                client.activate_account(match.group(1))
                client.login(email, "alphabet11")
                log(f"[+] Account logged in: {email}")
        except KeyboardInterrupt:
            sys.exit()
        except Exception as e:
            log(f"[!] Account/login error: {e}")
            if args.use_tor:
                from stem import Signal
                from stem.control import Controller

                try:
                    with Controller.from_port(port=9051) as controller:
                        controller.authenticate()
                        controller.signal(Signal.NEWNYM)
                        time.sleep(controller.get_newnym_wait())
                        log("[+] Tor identity changed")
                except Exception as e2:
                    log(f"[!] Tor change failed: {e2}")
            continue
        else:
            break


# -----------------------------
# Domain creation
# -----------------------------
with open("words.txt", "r") as f:
    WORDLIST = [line.strip().lower() for line in f if line.strip()]


def send_discord_notification(webhook_url, domain_url):
    if not webhook_url:
        return
    try:
        data = {
            "username": "Bromine Link Gen",
            "avatar_url": "https://avatars.githubusercontent.com/u/214591804?s=200&v=4",
            "content": f"New domain created: {domain_url}",
        }
        response = requests.post(webhook_url, json=data)
        if response.status_code not in (204, 200):
            log(f"[!] Discord webhook failed: {response.text}")
    except Exception as e:
        log(f"[!] Discord webhook error: {e}")


def createdomain(headergen):
    while True:
        try:
            capcha = solve(getcaptcha()) if args.auto else input("Captcha: ")
            random_domain_id = random.choice(domainlist)
            subdomainy = (
                random.choice(WORDLIST)
                if args.subdomains == "random"
                else random.choice(args.subdomains.split(","))
            )

            # Resolve real domain ID using the TLD name
            tld = domainnames[domainlist.index(random_domain_id)]
            real_domain_id = find_domain_id(tld, headergen)
            if not real_domain_id:
                log(f"[!] Could not resolve domain ID for {tld}, skipping...")
                continue

            client.create_subdomain(
                capcha, args.type, subdomainy, real_domain_id, args.ip
            )
            domain_url = f"http://{subdomainy}.{tld}"
            with open(args.outfile, "a") as f:
                f.write(domain_url + "\n")
            log(f"[+] Domain created: {domain_url}")
            send_discord_notification(args.webhook, domain_url)
        except KeyboardInterrupt:
            sys.exit()
        except Exception as e:
            log(f"[!] Domain creation error: {e}")
            continue
        else:
            break


def createlinks(number, headergen):
    for i in range(number):
        if i % 5 == 0 and args.use_tor:
            from stem import Signal
            from stem.control import Controller

            with Controller.from_port(port=9051) as controller:
                controller.authenticate()
                controller.signal(Signal.NEWNYM)
                time.sleep(controller.get_newnym_wait())
            login(headergen())
        createdomain(headergen)


# -----------------------------
# Initialization
# -----------------------------
non_random_domain_id = None


def finddomains(pagearg, headergen):
    print(pagearg)
    for page in pagearg.split(","):
        print(page)
        getdomains(page, headergen)


def init():
    global non_random_domain_id
    import random_header_generator

    headergen = random_header_generator.HeaderGenerator()

    # Use domains from file if specified
    if args.domains_file and os.path.isfile(args.domains_file):
        load_domains_from_file(args.domains_file)
    else:
        # Fallback to scraping
        log("[*] No domains_file found, falling back to web scraping...")
        if args.single_tld:
            non_random_domain_id = find_domain_id(args.single_tld, headergen)
            if not non_random_domain_id:
                log("[!] Could not find single domain ID, exiting.")
                sys.exit(1)
            domainlist = [non_random_domain_id]
            domainnames = [args.single_tld]
        else:
            finddomains(args.pages, headergen)

    createlinks(args.number, headergen)


if __name__ == "__main__":
    init()
