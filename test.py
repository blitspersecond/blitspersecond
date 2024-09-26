import numpy as np
import pyspng

with open("img/testcard.png", "rb") as fin:
    nparr = pyspng.load(fin.read())

print(nparr.shape)
