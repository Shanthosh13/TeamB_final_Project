import pdfplumber

def extract_text(file):

    text=""

    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            t=page.extract_text()
            if t:
                text+=t

    return text[:2000]