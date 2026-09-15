import unittest
import numpy as np
import pandas as pd
import torch
from train_tabm import fit_encoder, encode, member_loss, Network, predict, masks, metric


class Tests(unittest.TestCase):
    def test_train_only_encoder(self):
        train=pd.DataFrame({'num':[1.,2.,np.nan],'cat':['a','b',None]})
        b=fit_encoder(train,list(train))
        x,c=encode(pd.DataFrame({'num':[np.inf,1000.],'cat':['future','a']}),b)
        self.assertEqual(c.tolist(),[[0],[1]])
        self.assertEqual(x[0].tolist(),[0.,1.])
        self.assertNotIn('future',b['categories']['cat'])
        self.assertTrue(torch.isfinite(x).all())

    def test_weighted_loss_identity(self):
        p=torch.tensor([[1.,3.],[-1.,4.]])
        y=torch.tensor([0.,7.]);s=torch.tensor([1.,10.])
        self.assertTrue(torch.allclose(member_loss(p,y,s),(p*s[:,None]-y[:,None]).abs().mean()))

    def test_temporal_eligibility(self):
        f=pd.DataFrame({'year_month':pd.to_datetime(['2018-01-01','2018-02-01','2025-04-01','2025-09-01']),
                        'forecast_month':pd.to_datetime(['2018-02-01','2018-03-01','2025-05-01','2025-10-01']),
                        'historical_training_eligible':[False,True,True,True]})
        tr,va=masks(f,['2025-04-01','2025-05-01','2025-06-01'])
        self.assertEqual(tr.tolist(),[False,True,False,False])
        self.assertEqual(va.tolist(),[False,False,True,False])

    def test_training_and_inference(self):
        torch.manual_seed(42)
        f=pd.DataFrame({'num':np.arange(32,dtype=float),'cat':['a','b']*16})
        b=fit_encoder(f,list(f));x,c=encode(f,b);model=Network(b)
        opt=torch.optim.AdamW(model.parameters(),lr=.001)
        old=next(model.parameters()).detach().clone()
        p=model(x,c)
        self.assertEqual(tuple(p.shape),(32,4))
        loss=member_loss(p,torch.ones(32),torch.ones(32))
        loss.backward();opt.step()
        self.assertFalse(torch.equal(old,next(model.parameters()).detach()))
        model.eval()
        with torch.no_grad(): expected=model(x,c).mean(1).numpy().clip(0)
        np.testing.assert_allclose(predict(model,x,c,np.ones(32)),expected)
        self.assertTrue(np.isfinite(metric(np.ones(32),expected)['WAPE']))


if __name__=='__main__': unittest.main()
