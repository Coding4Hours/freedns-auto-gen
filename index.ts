import { Page } from "puppeteer";
import * as cheerio from "cheerio";
import puppeteer from "puppeteer-extra";
import StealthPlugin from "puppeteer-extra-plugin-stealth";
import { faker } from "@faker-js/faker";
import { newEmail, fetchEmails } from "temp-mail-io";
import { execSync } from "child_process";
import { readFileSync } from "fs";

const args = {
	number: 5,
	ip: "129.153.136.235",
	// proxy: null,
	// proxy: "socks5://127.0.0.1:9050",
	proxy: "socks4://98.182.147.97:4145",
	outfile: "domainlist.txt",
	pages: "1-10",
	domain_type: null,
	webhook: "",
	auto: true,
};

puppeteer.use(StealthPlugin());

const data = readFileSync("proxies.txt", "utf8");
const lines = data.split(/\r?\n/);

const random_proxy = lines[Math.floor(Math.random() * lines.length)];

console.log(random_proxy);

const browser = await puppeteer.launch({
	args: [
		"--ignore-certificate-errors",
		"--no-sandbox",
		...(args.proxy || random_proxy
			? [`--proxy-server=${args.proxy || random_proxy}`]
			: []),
	],
	headless: true,
});

const domainlist: string[] = [];
const domainnames: string[] = [];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function get_page_list(arg: string): number[] {
	const cleanArg = arg.trim();

	if (!cleanArg) process.exit(1);

	const pageList: number[] = [];
	const items = cleanArg.split(",");

	for (let item of items) {
		item = item.trim();
		if (!item) {
			continue;
		}

		if (item.includes("-")) {
			const parts = item.split("-").map((x) => parseInt(x, 10));

			if (parts.length !== 2 || parts.some(isNaN)) {
				process.exit(1);
			}

			const [sp, ep] = parts;

			if (sp === undefined || ep === undefined || sp < 1 || sp > ep) {
				process.exit(1);
			}

			for (let i = sp; i <= ep; i++) {
				pageList.push(i);
			}
		} else {
			const p = parseInt(item, 10);

			if (isNaN(p) || p < 1) process.exit(1);
			pageList.push(p);
		}
	}

	const uniqueList = Array.from(new Set(pageList));
	return uniqueList.sort((a, b) => a - b);
}

function prompt(title: string) {
	return execSync(`gum input --placeholder "${title}"`, {
		stdio: ["inherit", "pipe", "inherit"],
	}).toString();
}

async function detect_error(page: Page) {
	try {
		const errorMessage = await page.$eval(
			"table[width='95 % '] td[bgcolor='#eeeeee']",
			(el) => el.textContent.trim(),
		);

		return errorMessage;
	} catch (error) {
		return null;
	}
}

async function handle_captcha(page: Page) {
	const captcha = await page.$("#captcha");

	await captcha?.screenshot({
		path: "captcha.png",
	});

	const captcha_solved = prompt("captcha(inside captcha.png)").trim();
	await page.locator("[name='captcha_code']").fill(captcha_solved);

	if (
		await detect_error(page) ===
		"The security code was incorrect, please try again."
	)
		handle_captcha(page);
	return captcha_solved;
}

async function create_account(
	firstname: string,
	lastname: string,
	username: string,
	password: string,
	email: string,
) {
	const page = await browser.newPage();
	await page.goto("https://freedns.afraid.org/signup/?plan=starter");

	await sleep(2000);
	await page.locator("[name='firstname']").fill(firstname);
	await page.locator("[name='lastname']").fill(lastname);
	await page.locator("[name='username']").fill(username);
	await page.locator("[name='password']").fill(password);
	await page.locator("[name='password2']").fill(password);
	await page.locator("[name='email']").fill(email);

	const captcha_solved = await handle_captcha(page);
	console.log(captcha_solved);

	await page.locator("[name='tos']").click();

	// press activation email
	await page.locator("[name='send']").click();

	const error = await detect_error(page);
	if (error != null) throw new Error(error);
}

async function activate_account(activation_code: string) {
	const activate_url = `https://freedns.afraid.org/signup/activate.php?${activation_code}`;
	const page = await browser.newPage();
	await page.goto(activate_url);

	const error = await detect_error(page);
	if (error != null) throw new Error(error);
}


async function create_subdomain(
	subdomain: string,
	domain_id: string,
	destination: string,
) {
	const page = await browser.newPage();
	await page.goto(
		`https://freedns.afraid.org/subdomain/edit.php?edit_domain_id=${domain_id}`,
	);

	await page.locator("[name='subdomain']").fill(subdomain);
	await page.locator("[name='address']").fill(destination);
	const captcha_solved = await handle_captcha(page);
	console.log(captcha_solved);

	await page.locator("[name='send']").click();

	const error = await detect_error(page);
	if (error != null) throw new Error(error);
}

async function getdomains(arg: any) {
	try {
		const page = await browser.newPage();
		for (const sp of get_page_list(arg)) {
			await page.goto(
				`https://freedns.afraid.org/domain/registry/?page=${sp}&sort=2&q=`,
			);

			const html = await page.content();
			if (!html) {
				console.log(`[!] Blocked or failed to fetch page ${sp}`);
			}

			const $ = cheerio.load(html);

			$("tr").each((_index, element) => {
				const row = $(element);

				const link = row.find('a[href*="edit_domain_id="]');

				if (link.length === 0) return;

				const href = link.attr("href") || "";
				const idMatch = href.match(/edit_domain_id=(\d+)/);
				const dom_id = idMatch ? idMatch[1] : null;

				const dom_name = link.text().trim();

				let dom_type: string | null = null;

				row.find("td").each((_, td) => {
					const text = $(td).text().trim();
					if (text === "public" || text === "private") {
						dom_type = text;
					}
				});

				if (!dom_id || !dom_name || !dom_type) {
					return;
				}

				let shouldAppend = false;

				if (args.domain_type === "private" && dom_type === "private") {
					shouldAppend = true;
				} else if (args.domain_type === "public" && dom_type === "public") {
					shouldAppend = true;
				} else if (
					args.domain_type !== "private" &&
					args.domain_type !== "public"
				) {
					shouldAppend = true;
				}

				if (shouldAppend) {
					domainlist.push(dom_id);
					domainnames.push(dom_name);
				}
			});
		}
	} catch (e) {
		console.log(e);
	}
}

async function find_domain_id(domain_name: any) {
	try {
		const res = await fetch(
			`https://freedns.afraid.org/domain/registry/?page=1&q=${domain_name}`,
		);
		if (res.status != 200 || !res.text) {
			console.log(
				`[!] Blocked or failed to fetch domain ID for ${domain_name}`,
			);
			return null;
		}
		const html = await res.text();
		const pattern =
			/<a href=\/subdomain\/edit\.php\?edit_domain_id=([0-9]+)<\/a>/;
		const matches = html.match(pattern);
		if (matches) return matches[1];

		console.log(`[!] Domain ID not found for ${domain_name}`);
	} catch (e: any) {
		console.log(`[!] Error fetching domain ID for ${domain_name}: ${e}`);
		return null;
	}
}

async function loginn(): Promise<void> {
	while (true) {
		// --- Setup ---
		const email = await newEmail();
		const firstname = faker.person.firstName();
		const lastname = faker.person.lastName();
		const username =
			firstname + lastname + faker.number.int({ min: 100, max: 1000 });
		const password = faker.internet.password();
		const address = email.email;

		// --- Create Account ---
		await create_account(firstname, lastname, username, password, address);
		console.log(`Waiting for email on ${address}...`);

		// --- Activate Account ---
		let activated = false;

		while (!activated) {
			const emails = await fetchEmails(address);
			const content = emails[0]?.bodyText;
			const match = content?.match(/activate\.php\?([a-zA-Z0-9]+)/);

			if (match) {
				const activation_code = match[1];
				console.log(`Found activation code: ${activation_code}`);

				await activate_account(activation_code);

				console.log(`[+] Account logged in: ${address}`);

				activated = true;
				return;
				// break;
			} else {
				await sleep(5000);
			}
		}
	}
}

const getRandomChoice = <T>(arr: T[]): T => {
	return arr[Math.floor(Math.random() * arr.length)];
};

async function createdomain() {
	while (true) {
		try {
			const randomDomainId = getRandomChoice(domainlist).toString();

			const subdomainy = faker.word.words(1);

			await create_subdomain(subdomainy, randomDomainId, args.ip);

			const index = domainlist.indexOf(randomDomainId);
			const tld = domainnames[index];
			const domain_url = `http://${subdomainy}.${tld}`;

			console.log(`[+] Domain created: ${domain_url}`);
		} catch (e) {
			console.log(`[!] Domain creation error: ${e}`);
			continue;
		}
	}
}

async function finddomains(pagearg) {
	const pages = pagearg.split(",");
	for (const page of pages) {
		await getdomains(page.trim());
	}
}

async function createlinks(number) {
	for (let i = 0; i < number; i++) {
		if (i % 5 == 0) await loginn();

		createdomain();
	}
}

await finddomains(args.pages);
console.log(domainlist);
await createlinks(args.number);
