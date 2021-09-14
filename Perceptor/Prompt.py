from pytti import *
import torch
from torch import nn
from torch.nn import functional as F
import re
from CLIP import clip
import pytti
from PIL import Image
from pytti.Image import RGBImage

def spherical_dist_loss(x, y):
  x = F.normalize(x, dim=-1)
  y = F.normalize(y, dim=-1)
  return (x - y).norm(dim=-1).div(2).arcsin().pow(2).mul(2)

def make_mask(mask_fun, thresh):
  return lambda pos, size: mask_fun(size,pos,thresh)

def mask_right(pos, size, thresh = 0.5):
  cent = pos[...,0]+size[...,0]/2
  return cent.lt(thresh).float()

def mask_left(pos, size, thresh = 0.5):
  cent = pos[...,0]+size[...,0]/2
  return cent.gt(thresh).float()

def mask_down(pos, size, thresh = 0.5):
  cent = pos[...,1]+size[...,1]/2
  return cent.lt(thresh).float()

def mask_up(pos, size, thresh = 0.5):
  cent = pos[...,1]+size[...,1]/2
  return cent.gt(thresh).float()

def mask_all(pos, size, thresh = 0.5):
  return torch.zeros_like(size[...,0]).fill_(float('-inf'))

MASK_DICT  ={'a':mask_all, 'r':mask_right, 'l':mask_left, 'd':mask_down, 'u':mask_up}

def parse(string, split, defaults):
  tokens = re.split(split, string, len(defaults)-1)
  tokens = tokens+defaults[len(tokens):]
  return tokens

class Prompt(nn.Module):
  def __init__(self, embeds, weight, stop, text, prompt_string, mask = mask_all):
    super().__init__()
    if embeds is not None:
      self.register_buffer('embeds',  embeds)
    self.register_buffer('weight', torch.as_tensor(weight))
    self.register_buffer('stop',   torch.as_tensor(stop))
    self.input_axes = ('n', 'c', 'i')
    self.prompt_string = prompt_string
    self.text = text
    self.mask = mask

  def __repr__(self):
    return self.prompt_string

  def __str__(self):
    return self.text

  def forward(self, embed, position, size):
    """
    input: (Tensor) input CLIP embedding
    returns the input's loss compared to the saved embedding
    """
    dists = spherical_dist_loss(embed, self.embeds)
    dists = dists * self.weight.sign()
    stops =  torch.maximum(self.mask(position, size)+self.weight.sign().clamp(max=0), self.stop)
    dists = self.weight.abs() * replace_grad(dists, torch.maximum(dists, stops))
    return dists.mean()

class MultiClipPrompt(Prompt):
  """
  Compares CLIP embeddings (text or image) to saved embeddings for multiple CLIP models simultaneously
  based on VQGAN+CLIP system by Katherine Crowson (https://github.com/crowsonkb)
  embed:  (Tensor) CLIP embeddings
  text:   (string) text representation
  weight: (nonzero float) overall strength of prompt. Negative weights negate the prompt
  stop:   (float in [-1,1], sign(stop) == sign(weight)) minimum comparison distance in CLIP embedding space
          regardless of sign, lesser stop values make the optimizer greedier and greater stop values make the optimizer lazier
          sign must match weight, so stop is in [-1,0) if weight < 0, or stop is in [0,1) if weight > 0
  """
  def __init__(self, prompt_string, perceptors=None, device=DEVICE):
    text, weight, stop = parse(prompt_string,':',['', '1', '-inf'])
    text   = text.strip()
    weight, direction, cutoff = parse(weight,'_',['1','a','0.5']) 
    weight = float(weight.strip())
    stop   = float(stop.strip())
    direction = direction.strip()
    cutoff = float(cutoff.strip())
    mask = make_mask(MASK_DICT[direction],cutoff)
    if perceptors is None:
      perceptors = pytti.Perceptor.CLIP_PERCEPTORS
    embeds = cat_with_pad([p.encode_text(clip.tokenize(text).to(device)).float() for p in perceptors])
    super().__init__(embeds, weight, stop, text, prompt_string, mask = mask)

from scipy.spatial.distance import cdist
from scipy.optimize import linear_sum_assignment

def minimize_average_distance(tensor_a, tensor_b, device=DEVICE):
  """
  tensor_a: pytorch tensor
  tensor_b: pytorch tensor
  returns: tensor of indicies in tensor_a which will minimize the euclidian distance between the elments of the two tensors
  """
  tensor_a = tensor_a.detach().cpu().numpy()
  tensor_b = tensor_b.detach().cpu().numpy()
  tensor_a = tensor_a.reshape(tensor_a.shape[0], -1)
  tensor_b = tensor_b.reshape(tensor_b.shape[0], -1)
  distances = cdist(tensor_a, tensor_b)
  row_ind, col_ind = linear_sum_assignment(distances)
  return torch.tensor(col_ind).long().to(device)

class MultiClipImagePrompt(Prompt):
  def __init__(self, embedder, prompt_string="IMAGE PROMPT", pil_image=None):
    text, weight, stop = parse(prompt_string,'(?<!^http)(?<!s):|:(?!//)',['', '1', '-inf'])
    text = text.strip()
    if pil_image is None:
      pil_image = Image.open(fetch(text)).convert("RGB")
    width, height = pil_image.size
    img = RGBImage(width, height)
    img.encode_image(pil_image)
    weight, direction, cutoff = parse(weight,'_',['1','a','0.5']) 
    weight = float(weight.strip())
    stop   = float(stop.strip())
    direction = direction.strip()
    cutoff = float(cutoff.strip())
    mask = make_mask(MASK_DICT[direction],cutoff)
    embeds, positions, sizes = embedder(img)
    embeds = embeds.detach()
    self.input_axes = ('n', 'c', 'i')
    embeds = format_input(embeds,embedder,self)
    super().__init__(embeds, weight, stop, text+" (semantic)", prompt_string, mask = mask)
    self.register_buffer('positions',format_input(positions,embedder,self))
    self.register_buffer('sizes'    ,format_input(sizes,embedder,self))

class LocationAwareMCIP(MultiClipImagePrompt):
  def forward(self, embed, position, size):
    """
    input: (Tensor) input CLIP embedding
    returns the input's loss compared to the saved embedding
    """
    cent_a = position + size/2
    cent_b = self.positions + self.sizes/2
    indices = minimize_average_distance(cent_a, cent_b)
    return super().forward(embed[indices], position, size)

