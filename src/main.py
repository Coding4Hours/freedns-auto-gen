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


# Configuration
class Args:
    number = 10
    ip = "129.153.136.235"  # Always use this IP
    proxy = None
    use_tor = True
    silent = False  # logs enabled
    outfile = "domainlist.txt"
    type = "A"
    pages = "1-10"
    subdomains = "random"
    auto = True
    domain_type = None
    single_tld = None


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
# Page parsing
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
    try:
        html = requests.get(
            f"https://freedns.afraid.org/domain/registry/?page=1&q={domain_name}",
            headers=headergen(),
        )
        if html.status_code != 200 or not html.text:
            log(f"[!] Blocked or failed to fetch domain ID for {domain_name}")
            return None
        html = html.text
        pattern = r"<a href=\/subdomain\/edit\.php\?edit_domain_id=([0-9]+)<\/a>"
        matches = re.findall(pattern, html)
        if matches:
            return matches[0]
        log(f"[!] Domain ID not found for {domain_name}")
    except Exception as e:
        log(f"[!] Error fetching domain ID for {domain_name}: {e}")
    return None


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

# Load a wordlist once at the start
with open("words.txt", "r") as f:
    WORDLIST = [line.strip().lower() for line in f if line.strip()]


def send_discord_notification(webhook_url, domain_url):
    if not webhook_url:
        return
    try:
        data = {
            "username": "Bromine Link Gen",  # <- correct
            "avatar_url": "https://avatars.githubusercontent.com/u/214591804?s=200&v=4",  # <- correct
            "content": f"New domain created: {domain_url}",
        }
        response = requests.post(webhook_url, json=data)
        if response.status_code != 204 and response.status_code != 200:
            log(f"[!] Discord webhook failed: {response.text}")
    except Exception as e:
        log(f"[!] Discord webhook error: {e}")


# -----------------------------
# Domain creation (modified)
# -----------------------------
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

            client.create_subdomain(
                capcha, args.type, subdomainy, random_domain_id, args.ip
            )

            tld = domainnames[domainlist.index(random_domain_id)]
            domain_url = f"http://{subdomainy}.{tld}"

            with open(args.outfile, "a") as f:
                f.write(domain_url + "\n")

            log(f"[+] Domain created: {domain_url}")

            # Send Discord notification
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
        createdomain(headergen())


# -----------------------------
# Initialization
# -----------------------------
non_random_domain_id = None


def finddomains(pagearg, headergen):
    for page in pagearg.split(","):
        getdomains(page, headergen)


def init():
    global non_random_domain_id
    import random_header_generator

    headergen = random_header_generator.HeaderGenerator()  # dict, not callable
    if args.single_tld:
        non_random_domain_id = find_domain_id(args.single_tld, headergen)
        if not non_random_domain_id:
            log("[!] Could not find single domain ID, exiting.")
            sys.exit(1)
    else:
        finddomains(args.pages, headergen)
    createlinks(args.number, headergen)


if __name__ == "__main__":
    init()
