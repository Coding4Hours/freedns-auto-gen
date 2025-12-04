from PIL import Image, ImageFilter
from io import BytesIO
import time
import requests
import re
import random
import sys
import lxml.html
import pytesseract
import os
import platform
import temp_mails
from selenium import webdriver
from selenium.webdriver.common.by import By


class Args:
    number = 5
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
    webhook = ""
    single_tld = None


args = Args()


# Load a wordlist once at the start
with open("words.txt", "r") as f:
    WORDLIST = [line.strip().lower() for line in f if line.strip()]


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


def get_captcha():
    captcha_url = "https://freedns.afraid.org/securimage/securimage_show.php"
    response = requests.get(captcha_url)
    return response.content


def create_account(firstname, lastname, username, password, email):
    driver = webdriver.Chrome()
    driver.get("https://freedns.afraid.org/signup/?plan=starter")

    driver.find_element(by=By.NAME, value="firstname").send_keys(firstname)
    driver.find_element(by=By.NAME, value="lastname").send_keys(lastname)
    driver.find_element(by=By.NAME, value="username").send_keys(username)
    driver.find_element(by=By.NAME, value="password").send_keys(password)
    driver.find_element(by=By.NAME, value="password2").send_keys(password)
    driver.find_element(by=By.NAME, value="email").send_keys(email)

    captcha_element = driver.find_element(By.ID, "captcha")

    image_content = captcha_element.screenshot_as_png
    with open("captcha.png", "wb") as f:
        f.write(image_content)

    image = Image.open(BytesIO(image_content))
    captcha = solve(image)

    driver.find_element(by=By.NAME, value="tos").click()
    driver.find_element(by=By.NAME, value="captcha_code").send_keys(captcha)
    # press activation email
    driver.find_element(by=By.NAME, value="send").click()


def activate_account(activation_code):
    activate_url = f"https://freedns.afraid.org/signup/activate.php?{activation_code}"

    response = requests.get(activate_url, allow_redirects=False)
    if response.status_code != 302:
        error_message = detect_error(response.text)
        raise RuntimeError("Account activation failed. Error: " + error_message)


def login(username, password):
    login_url = "https://freedns.afraid.org/zc.php?step=2"
    payload = {
        "username": username,
        "password": password,
        "remember": "1",
        "submit": "Login",
        "remote": "",
        "from": "",
        "action": "auth",
    }

    response = requests.post(login_url, data=payload, allow_redirects=False)
    if response.status_code != 302:
        error_message = detect_error(response.text)
        raise RuntimeError("Login failed. Error: " + error_message)


def detect_error(self, html):
    document = lxml.html.fromstring(html)

    table = document.cssselect('table[width="95%"]')[0]
    cell = table.cssselect('td[bgcolor="#eeeeee"]')[0]
    error_message = cell.text_content()
    return error_message.strip()


def create_subdomain(captcha_code, record_type, subdomain, domain_id, destination):
    create_subdomain_url = "https://freedns.afraid.org/subdomain/save.php?step=2"
    payload = {
        "type": record_type,
        "subdomain": subdomain,
        "domain_id": domain_id,
        "address": destination,
        "ttlalias": "For+our+premium+supporters",
        "captcha_code": captcha_code,
        "ref": "",
        "send": "Save!",
    }

    response = requests.post(create_subdomain_url, data=payload, allow_redirects=False)
    if response.status_code != 302:
        error_message = detect_error(response.text)
        raise RuntimeError("Failed to create subdomain. Error: " + error_message)


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


def getdomains(arg):
    global domainlist, domainnames
    driver = webdriver.Chrome()

    try:
        for sp in getpagelist(arg):
            try:
                driver.get(
                    f"https://freedns.afraid.org/domain/registry/?page={sp}&sort=2&q="
                )

                html = driver.page_source
                if not html:
                    log(f"[!] Blocked or failed to fetch page {sp}")
                    continue

                pattern = r"edit_domain_id=(\d+)[^>]*>([\w.-]+)<\/a>(?:.*?)<td>(public|private)<\/td>"

                matches = re.findall(pattern, html, re.DOTALL)

                filtered_matches = []
                for m in matches:
                    dom_id, dom_name, dom_type = m

                    if args.domain_type == "private" and dom_type == "private":
                        filtered_matches.append((dom_id, dom_name))
                    elif args.domain_type == "public" and dom_type == "public":
                        filtered_matches.append((dom_id, dom_name))
                    elif args.domain_type not in ["private", "public"]:  # "all"
                        filtered_matches.append((dom_id, dom_name))

                domainlist.extend([m[0] for m in filtered_matches])
                domainnames.extend([m[1] for m in filtered_matches])

            except Exception as e:
                log(f"[!] Error fetching page {sp}: {e}")

    finally:
        driver.quit()


def find_domain_id(domain_name):
    try:
        html = requests.get(
            f"https://freedns.afraid.org/domain/registry/?page=1&q={domain_name}",
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
    return Image.open(BytesIO(get_captcha()))


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


def generate_random_string():
    return random.choice(WORDLIST)


# -----------------------------
# Login & account creation
# -----------------------------


def loginn():
    while True:
        try:
            mail = temp_mails.Generator_email()
            email = mail.email
            username = f"{generate_random_string()}{generate_random_string()}{
                ''.join(str(random.randint(1, 9)) for _ in range(3))
            }"
            print(username)
            create_account(
                generate_random_string(),
                generate_random_string(),
                username,
                "alphabet11",
                email,
            )
            text = mail.wait_for_new_email(timeout=30)
            content = str(mail.get_mail_content(mail_id=text["id"]))
            match = re.search(r'\?([^">]+)"', content)
            if match:
                activate_account(match.group(1))
                login(email, "alphabet11")
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
        if response.status_code != 204 and response.status_code != 200:
            log(f"[!] Discord webhook failed: {response.text}")
    except Exception as e:
        log(f"[!] Discord webhook error: {e}")


# -----------------------------
# Domain creation (modified)
# -----------------------------
def createdomain():
    while True:
        try:
            capcha = solve(getcaptcha()) if args.auto else input("Captcha: ")
            random_domain_id = random.choice(domainlist)
            subdomainy = (
                random.choice(WORDLIST)
                if args.subdomains == "random"
                else random.choice(args.subdomains.split(","))
            )

            create_subdomain(capcha, args.type, subdomainy, random_domain_id, args.ip)

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


def createlinks(number):
    for i in range(number):
        if i % 5 == 0 and args.use_tor:
            from stem import Signal
            from stem.control import Controller

            with Controller.from_port(port=9051) as controller:
                controller.authenticate()
                controller.signal(Signal.NEWNYM)
                time.sleep(controller.get_newnym_wait())
            loginn()
        createdomain()


# -----------------------------
# Initialization
# -----------------------------
non_random_domain_id = None


def finddomains(pagearg):
    for page in pagearg.split(","):
        getdomains(page)


def init():
    global non_random_domain_id

    if args.single_tld:
        non_random_domain_id = find_domain_id(args.single_tld)
        if not non_random_domain_id:
            log("[!] Could not find single domain ID, exiting.")
            sys.exit(1)
    else:
        finddomains(args.pages)
    createlinks(args.number)


if __name__ == "__main__":
    init()
