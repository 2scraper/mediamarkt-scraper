# MediaMarkt Web Scraper

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python 3.8+">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/Playwright-✓-brightgreen.svg" alt="Playwright">
  <img src="https://img.shields.io/badge/Selenium-✓-brightgreen.svg" alt="Selenium">
  <img src="https://img.shields.io/badge/Puppeteer-✓-brightgreen.svg" alt="Puppeteer">
</p>

A high-performance, production-ready web scraper for extracting product data from MediaMarkt. Supports all product categories with advanced anti-detection features, captcha solving, and proxy rotation.

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Multiple Frameworks** | Choose between Playwright (recommended), Selenium, or Puppeteer |
| **All Categories** | Scrape all 17+ MediaMarkt product categories |
| **Anti-Detection** | Built-in fingerprint spoofing and stealth mode |
| **Captcha Solving** | Automatic solving via [2captcha.com](https://2captcha.com) |
| **Proxy Support** | Residential proxies via [2prx.com](https://2prx.com) |
| **Flexible Output** | Export to JSON or CSV formats |
| **Pagination** | Automatic handling of multi-page results |
| **Detailed Scraping** | Optional deep scraping for full product specifications |

## 📦 Installation

### Quick Start

```bash
# Clone the repository
git clone https://github.com/2scraper/mediamarkt-scraper.git
cd mediamarkt-scraper

# Install dependencies
pip install -r requirements.txt

# For Playwright, install browsers
playwright install chromium
```

### Install Specific Framework

```bash
# Playwright only (recommended)
pip install playwright 2captcha-python
playwright install chromium

# Selenium only
pip install selenium webdriver-manager selenium-stealth 2captcha-python

# Puppeteer only
pip install pyppeteer 2captcha-python
```

## 🚀 Quick Start

### Basic Usage

```bash
# Using Playwright (recommended)
python scrapers/mediamarkt_playwright.py

# Using Selenium
python scrapers/mediamarkt_selenium.py

# Using Puppeteer
python scrapers/mediamarkt_puppeteer.py
```

### With Proxy and Captcha Solving

```bash
python scrapers/mediamarkt_playwright.py \
  --proxy-host proxy.2prx.com \
  --proxy-port 8080 \
  --proxy-user YOUR_USER \
  --proxy-pass YOUR_PASS \
  --captcha-key YOUR_2CAPTCHA_API_KEY
```

### Scrape Specific Categories

```bash
python scrapers/mediamarkt_playwright.py --categories tv-audio,computing,gaming
```

### Export to CSV

```bash
python scrapers/mediamarkt_playwright.py --format csv --output-dir ./data
```

## 📋 Available Categories

| Key | Category |
|-----|----------|
| `tv-audio` | TV & Audio |
| `computing` | Computer & Hardware |
| `smartphones` | Smartphones & Navigation |
| `gaming` | Gaming & Entertainment |
| `photo-video` | Photo & Video |
| `household` | Household Appliances |
| `kitchen` | Kitchen Appliances |
| `personal-care` | Health & Personal Care |
| `smart-home` | Smart Home |
| `wearables` | Wearables & Sports |
| `car-tech` | Car, Motorcycle & Bike |
| `office` | Office Supplies |
| `diy-garden` | DIY & Garden |
| `toys` | Toys |
| `music-instruments` | Musical Instruments |
| `movies-music` | Movies, Music & Games |
| `accessories` | Accessories |

## ⚙️ Configuration Options

### Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--format` | Output format (json/csv) | `json` |
| `--output-dir` | Output directory | `./output` |
| `--categories` | Comma-separated categories | All |
| `--max-pages` | Max pages per category (0=unlimited) | `0` |
| `--details` | Scrape full product details | `False` |
| `--headless` | Run in headless mode | `True` |
| `--delay-min` | Minimum delay between requests (sec) | `1.0` |
| `--delay-max` | Maximum delay between requests (sec) | `3.0` |

### Proxy Options (2prx.com)

| Argument | Description |
|----------|-------------|
| `--proxy-host` | Proxy hostname |
| `--proxy-port` | Proxy port |
| `--proxy-user` | Proxy username |
| `--proxy-pass` | Proxy password |

### Captcha Options (2captcha.com)

| Argument | Description |
|----------|-------------|
| `--captcha-key` | Your 2captcha.com API key |

### Environment Variables

You can also use environment variables:

```bash
export TWOCAPTCHA_API_KEY="your_api_key"
export PROXY_HOST="proxy.2prx.com"
export PROXY_PORT="8080"
export PROXY_USER="your_user"
export PROXY_PASS="your_pass"
```

## 📊 Output Data Structure

### JSON Output

```json
{
  "id": "2765432",
  "name": "SAMSUNG Galaxy S24 Ultra 256GB Titanium Black",
  "brand": "SAMSUNG",
  "price": 1449.99,
  "original_price": 1549.99,
  "currency": "EUR",
  "availability": "Available online",
  "rating": 4.8,
  "reviews_count": 342,
  "url": "https://www.mediamarkt.de/product/_samsung-galaxy-s24-ultra-2765432.html",
  "image_url": "https://assets.mediamarkt.de/...",
  "category": "smartphones",
  "description": "...",
  "specifications": {
    "Display": "6.8\" Dynamic AMOLED 2X",
    "Processor": "Snapdragon 8 Gen 3",
    "RAM": "12 GB",
    "Storage": "256 GB"
  },
  "ean": "8806095280073",
  "sku": "2765432",
  "scraped_at": "2024-01-15T14:30:00.000Z"
}
```

### CSV Output

All fields are flattened, with `specifications` serialized as JSON string.

## 🛡️ Anti-Detection Features

### Fingerprint Spoofing

- Randomized User-Agent strings
- Variable viewport sizes
- Realistic browser plugins
- WebGL vendor/renderer spoofing
- Timezone and locale matching

### Stealth Mode

- Webdriver detection bypass
- Chrome automation flags removal
- Permission API overrides
- Console.log filtering

### Best Practices

```bash
# Use with delays and proxies for production scraping
python scrapers/mediamarkt_playwright.py \
  --delay-min 2.0 \
  --delay-max 5.0 \
  --proxy-host proxy.2prx.com \
  --proxy-port 8080
```

## 🔑 Captcha Solving with 2captcha.com

This scraper integrates with [2captcha.com](https://2captcha.com) for automatic captcha solving.

### Supported Captcha Types

- ✅ reCAPTCHA v2
- ✅ reCAPTCHA v3
- ✅ hCaptcha
- ✅ Cloudflare Turnstile

### Setup

1. Register at [2captcha.com](https://2captcha.com)
2. Get your API key from the dashboard
3. Use with `--captcha-key YOUR_API_KEY`

## 🌐 Proxy Setup with 2prx.com

For reliable scraping, use residential proxies from [2prx.com](https://2prx.com).

### Configuration

```bash
python scrapers/mediamarkt_playwright.py \
  --proxy-host eu-proxy.2prx.com \
  --proxy-port 8080 \
  --proxy-user your_username \
  --proxy-pass your_password
```

### Benefits

- German residential IPs for MediaMarkt
- Automatic rotation
- High success rate
- No IP blocks

## 🔒 Anti-Detect Browser Integration

For maximum stealth, consider using our anti-detect browser service for:

- Complete browser fingerprint control
- Hardware-level spoofing
- Cookie management
- Session persistence

Contact us at [2captcha.com](https://2captcha.com) for enterprise solutions.

## 💻 Python API Usage

```python
import asyncio
from mediamarkt_playwright import MediaMarktScraper, ScraperConfig

async def main():
    config = ScraperConfig(
        captcha_api_key="YOUR_2CAPTCHA_KEY",
        proxy_host="proxy.2prx.com",
        proxy_port=8080,
        proxy_username="user",
        proxy_password="pass",
        output_format="json",
        categories=["tv-audio", "gaming"],
        max_pages_per_category=5,
    )
    
    scraper = MediaMarktScraper(config)
    await scraper.scrape(scrape_details=True)
    scraper.save_results("products.json")
    
    # Access products directly
    for product in scraper.products:
        print(f"{product.name}: €{product.price}")

asyncio.run(main())
```

## 🐳 Docker Usage

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt
RUN playwright install chromium
RUN playwright install-deps

CMD ["python", "scrapers/mediamarkt_playwright.py"]
```

```bash
docker build -t mediamarkt-scraper .
docker run -v $(pwd)/output:/app/output mediamarkt-scraper \
  --captcha-key YOUR_KEY \
  --proxy-host proxy.2prx.com
```

## 📈 Performance Tips

1. **Use Playwright** - It's the fastest and most reliable
2. **Enable headless mode** - Better performance
3. **Use proxies** - Avoid IP blocks with [2prx.com](https://2prx.com)
4. **Set appropriate delays** - 2-5 seconds between requests
5. **Scrape during off-peak hours** - Better success rate
6. **Use captcha solving** - [2captcha.com](https://2captcha.com) for uninterrupted scraping

## ⚠️ Legal Disclaimer

This scraper is provided for educational and research purposes. Users are responsible for:

- Complying with MediaMarkt's Terms of Service
- Respecting robots.txt directives
- Following applicable data protection laws (GDPR)
- Using the data ethically and legally

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 Links

- **Captcha Solving**: [2captcha.com](https://2captcha.com)
- **Proxy Service**: [2prx.com](https://2prx.com)
- **Issues**: [GitHub Issues](https://github.com/2scraper/mediamarkt-scraper/issues)
- **Documentation**: [Wiki](https://github.com/2scraper/mediamarkt-scraper/wiki)

---

<p align="center">
  Made with ❤️ by <a href="https://2captcha.com">2captcha.com</a>
</p>
